import re
import secrets
import uuid
from datetime import timedelta
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from services.reservations.stockage import (
    annuler_reservation,
    billets_publics,
    date_iso,
    enregistrer_evenement,
    lire_date,
    maintenant,
)

routeur = APIRouter()

DUREE_BLOCAGE_S = 300
DELAI_ANNULATION = timedelta(hours=1)
FUSEAU = ZoneInfo("Europe/Paris")
PLACE = re.compile(r"^([A-Z]+)([1-9][0-9]*)$")
TARIFS_AVEC_JUSTIFICATIF = {"reduit", "enfant"}

# Libère un blocage seulement s'il appartient encore à ce client.
SCRIPT_LIBERATION = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


# L'identité vient du prestataire d'authentification simulé.
def client_authentifie(x_client_id: Annotated[str, Header(min_length=1)]) -> str:
    return x_client_id


def agent_authentifie(x_role: Annotated[str, Header()] = "") -> None:
    if x_role != "agent":
        raise HTTPException(403, detail="Réservé aux agents d'accueil.")


def executer_transaction(request: Request, fonction):
    with request.app.state.db.client.start_session() as session:
        return session.with_transaction(fonction)


def cle_blocage(request: Request, seance_id: str, place: str) -> str:
    return f"{request.app.state.redis_prefix}:blocage:{seance_id}:{place}"


def place_existe(plan: dict[str, Any], place: str) -> bool:
    correspondance = PLACE.match(place)
    if correspondance is None:
        return False
    rangee, numero = correspondance.group(1), int(correspondance.group(2))
    return rangee in plan["rangees"] and numero <= plan["places_par_rangee"]


def charger_seance(request: Request, seance_id: str) -> dict[str, Any]:
    seance = request.app.state.db.seances_replica.find_one({"seance_id": seance_id}, {"_id": 0})
    if seance is None:
        raise HTTPException(404, detail="Séance inconnue.")
    return seance


def verifier_seance_ouverte(seance: dict[str, Any] | None) -> None:
    if seance is None or seance["statut"] != "programmee":
        raise HTTPException(409, detail="La séance est annulée.")
    if lire_date(seance["debut"]) <= maintenant():
        raise HTTPException(409, detail="La séance a déjà commencé.")


def vue_reservation(reservation: dict[str, Any]) -> dict[str, Any]:
    vue = {cle: valeur for cle, valeur in reservation.items() if cle != "_id"}
    vue["billets"] = billets_publics(reservation["billets"])
    return vue


class DemandeBlocage(BaseModel):
    seance_id: str
    place: str


@routeur.post("/blocages", status_code=201)
def bloquer_place(
    demande: DemandeBlocage, request: Request, client_id: str = Depends(client_authentifie)
) -> dict[str, Any]:
    seance = charger_seance(request, demande.seance_id)
    verifier_seance_ouverte(seance)
    if not place_existe(seance["plan"], demande.place):
        raise HTTPException(422, detail="Cette place n'existe pas dans le plan de la salle.")
    if request.app.state.db.billets_par_seance.find_one(
        {"seance_id": demande.seance_id, "place": demande.place}
    ):
        raise HTTPException(409, detail="La place est déjà vendue.")

    redis = request.app.state.redis
    cle = cle_blocage(request, demande.seance_id, demande.place)
    if not redis.set(cle, client_id, nx=True, ex=DUREE_BLOCAGE_S) and redis.get(cle) != client_id:
        raise HTTPException(409, detail="La place est bloquée par un autre client.")
    expiration = maintenant() + timedelta(seconds=max(redis.ttl(cle), 0))
    return {"seance_id": demande.seance_id, "place": demande.place, "bloquee_jusqu_a": date_iso(expiration)}


class BilletDemande(BaseModel):
    place: str
    tarif: Literal["plein", "reduit", "enfant"]
    titulaire_id: str | None = None


class DemandeReservation(BaseModel):
    seance_id: str
    billets: Annotated[list[BilletDemande], Field(min_length=1)]
    paiement_id: Annotated[str, Field(min_length=1)]
    demande_id: Annotated[str, Field(min_length=1)]


def reservation_existante(request: Request, demande: DemandeReservation, client_id: str) -> dict[str, Any] | None:
    reservation = request.app.state.db.reservations.find_one({"demande_id": demande.demande_id})
    if reservation is not None and reservation["acheteur_id"] != client_id:
        raise HTTPException(409, detail="Cette demande appartient à un autre client.")
    return reservation


@routeur.post("/reservations", status_code=201)
def confirmer_reservation(
    demande: DemandeReservation,
    request: Request,
    response: Response,
    client_id: str = Depends(client_authentifie),
) -> dict[str, Any]:
    db, redis = request.app.state.db, request.app.state.redis

    existante = reservation_existante(request, demande, client_id)
    if existante is not None:
        response.status_code = 200
        return vue_reservation(existante)
    if db.reservations.find_one({"paiement_id": demande.paiement_id}):
        raise HTTPException(409, detail="Ce paiement est déjà utilisé par une autre demande.")

    places = [billet.place for billet in demande.billets]
    if len(set(places)) != len(places):
        raise HTTPException(422, detail="Les places demandées doivent être distinctes.")
    seance = charger_seance(request, demande.seance_id)
    verifier_seance_ouverte(seance)
    for billet in demande.billets:
        if not place_existe(seance["plan"], billet.place):
            raise HTTPException(422, detail=f"La place {billet.place} n'existe pas.")
        if billet.tarif not in seance["tarifs"]:
            raise HTTPException(422, detail=f"Le tarif {billet.tarif} n'existe pas pour cette séance.")
        if redis.get(cle_blocage(request, demande.seance_id, billet.place)) != client_id:
            raise HTTPException(409, detail=f"La place {billet.place} n'est pas bloquée par ce client.")

    date = maintenant()
    suffixe = uuid.uuid4().hex[:12]
    reservation_id = f"res_{suffixe}"
    billets = [
        {
            "billet_id": f"bil_{suffixe}_{rang}",
            "place": billet.place,
            "tarif": billet.tarif,
            "prix": seance["tarifs"][billet.tarif],
            "titulaire_id": billet.titulaire_id or client_id,
            "jeton_qr": secrets.token_urlsafe(24),
            "statut": "payee",
            "justificatif_verifie": False,
            "valide_le": None,
        }
        for rang, billet in enumerate(demande.billets, start=1)
    ]
    montant = round(sum(billet["prix"] for billet in billets), 2)
    # Paiement simulé : le prestataire accepte le montant total de la réservation.
    paiement = {"type": "paiement", "montant": montant, "reference": demande.paiement_id, "date": date_iso(date)}

    def transaction(session):
        # Écrire le réplica fait entrer cette confirmation en conflit avec une annulation de séance.
        seance_courante = db.seances_replica.find_one_and_update(
            {"seance_id": demande.seance_id, "statut": "programmee"},
            {"$inc": {"version_ventes": 1}},
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        verifier_seance_ouverte(seance_courante)
        # Un insert_one par place : l'index unique (seance_id, place) lève DuplicateKeyError.
        for b in billets:
            db.billets_par_seance.insert_one(
                {"seance_id": demande.seance_id, "place": b["place"], "reservation_id": reservation_id, "billet_id": b["billet_id"]},
                session=session,
            )
        reservation = {
            "reservation_id": reservation_id,
            "acheteur_id": client_id,
            "seance_id": demande.seance_id,
            "statut": "confirmee",
            "montant": montant,
            "billets": billets,
            "paiements": [paiement],
            "demande_id": demande.demande_id,
            "paiement_id": demande.paiement_id,
            "cree_le": date_iso(date),
        }
        db.reservations.insert_one(dict(reservation), session=session)
        db.billets_par_id.insert_many(
            [{"billet_id": b["billet_id"], "reservation_id": reservation_id} for b in billets], session=session
        )
        db.reservations_par_client.insert_one(
            {
                "acheteur_id": client_id,
                "reservation_id": reservation_id,
                "seance_id": demande.seance_id,
                "statut": "confirmee",
                "montant": montant,
                "cree_le": date_iso(date),
            },
            session=session,
        )
        db.billets_par_client.insert_many(
            [
                {
                    "titulaire_id": b["titulaire_id"],
                    "billet_id": b["billet_id"],
                    "reservation_id": reservation_id,
                    "seance_id": demande.seance_id,
                    "place": b["place"],
                    "tarif": b["tarif"],
                    "prix": b["prix"],
                    "jeton_qr": b["jeton_qr"],
                    "statut": "payee",
                }
                for b in billets
            ],
            session=session,
        )
        enregistrer_evenement(
            db,
            session,
            type="reservation.confirmee",
            type_objet="Reservation",
            id_objet=reservation_id,
            recepteurs=[],
            date=date,
            donnees={
                "acheteur_id": client_id,
                "seance_id": demande.seance_id,
                "statut": "confirmee",
                "montant": montant,
                "billets": billets_publics(billets),
            },
            contexte={"film_id": seance["film_id"], "debut_seance": seance["debut"]},
        )
        return reservation

    try:
        reservation = executer_transaction(request, transaction)
    except DuplicateKeyError as erreur:
        cles = (erreur.details or {}).get("keyPattern", {})
        if "place" in cles:
            raise HTTPException(409, detail="Une des places vient d'être vendue.") from erreur
        existante = reservation_existante(request, demande, client_id)
        if existante is not None:
            response.status_code = 200
            return vue_reservation(existante)
        raise HTTPException(409, detail="Ce paiement est déjà utilisé par une autre demande.") from erreur

    liberer = redis.register_script(SCRIPT_LIBERATION)
    for place in places:
        liberer(keys=[cle_blocage(request, demande.seance_id, place)], args=[client_id])
    return vue_reservation(reservation)


@routeur.post("/reservations/{reservation_id}/annulation")
def annuler_par_acheteur(
    reservation_id: str, request: Request, client_id: str = Depends(client_authentifie)
) -> dict[str, Any]:
    db = request.app.state.db

    def transaction(session):
        reservation = db.reservations.find_one({"reservation_id": reservation_id}, session=session)
        if reservation is None:
            raise HTTPException(404, detail="Réservation inconnue.")
        if reservation["acheteur_id"] != client_id:
            raise HTTPException(403, detail="Seul l'acheteur peut annuler cette réservation.")
        if reservation["statut"] == "annulee":
            return reservation
        seance = db.seances_replica.find_one({"seance_id": reservation["seance_id"]}, session=session)
        if lire_date(seance["debut"]) - maintenant() < DELAI_ANNULATION:
            raise HTTPException(409, detail="L'annulation doit intervenir au moins 1 heure avant la séance.")
        if any(billet["statut"] == "validee" for billet in reservation["billets"]):
            raise HTTPException(409, detail="Un billet de cette réservation a déjà été validé.")
        annulee = annuler_reservation(
            db, session, reservation, seance, motif="annulation_client", date=maintenant()
        )
        return annulee or db.reservations.find_one({"reservation_id": reservation_id}, session=session)

    return vue_reservation(executer_transaction(request, transaction))


class DemandeValidation(BaseModel):
    jeton_qr: Annotated[str, Field(min_length=1)]
    salle_id: str
    justificatif_verifie: bool = False


@routeur.post("/billets/{billet_id}/validation", dependencies=[Depends(agent_authentifie)])
def valider_billet(billet_id: str, demande: DemandeValidation, request: Request) -> dict[str, Any]:
    db = request.app.state.db
    lien = db.billets_par_id.find_one({"billet_id": billet_id})
    if lien is None:
        raise HTTPException(404, detail="Billet inconnu.")

    def transaction(session):
        date = maintenant()
        reservation = db.reservations.find_one({"reservation_id": lien["reservation_id"]}, session=session)
        billet = next(b for b in reservation["billets"] if b["billet_id"] == billet_id)
        if not secrets.compare_digest(billet["jeton_qr"], demande.jeton_qr):
            raise HTTPException(403, detail="Le QR code ne correspond pas à ce billet.")
        seance = db.seances_replica.find_one_and_update(
            {"seance_id": reservation["seance_id"], "statut": "programmee"},
            {"$inc": {"version_ventes": 1}},
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        if seance is None:
            raise HTTPException(409, detail="La séance est annulée.")
        if reservation["statut"] != "confirmee":
            raise HTTPException(409, detail="La réservation est annulée.")
        if billet["statut"] == "validee":
            raise HTTPException(409, detail="Ce billet a déjà été validé.")
        if seance["salle_id"] != demande.salle_id:
            raise HTTPException(409, detail=f"Ce billet est pour la salle {seance['salle_id']}.")
        if lire_date(seance["debut"]).astimezone(FUSEAU).date() != date.astimezone(FUSEAU).date():
            raise HTTPException(409, detail="Cette séance n'a pas lieu aujourd'hui.")
        if date >= lire_date(seance["fin"]):
            raise HTTPException(409, detail="La séance est terminée.")
        if billet["tarif"] in TARIFS_AVEC_JUSTIFICATIF and not demande.justificatif_verifie:
            raise HTTPException(409, detail="Le justificatif du tarif doit être vérifié.")

        mise_a_jour = db.reservations.find_one_and_update(
            {
                "reservation_id": reservation["reservation_id"],
                "statut": "confirmee",
                "billets": {"$elemMatch": {"billet_id": billet_id, "statut": "payee"}},
            },
            {
                "$set": {
                    "billets.$.statut": "validee",
                    "billets.$.valide_le": date_iso(date),
                    "billets.$.justificatif_verifie": demande.justificatif_verifie,
                }
            },
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        if mise_a_jour is None:
            raise HTTPException(409, detail="Ce billet a déjà été validé.")
        db.billets_par_client.update_one(
            {"titulaire_id": billet["titulaire_id"], "billet_id": billet_id},
            {"$set": {"statut": "validee", "valide_le": date_iso(date)}},
            session=session,
        )
        enregistrer_evenement(
            db,
            session,
            type="billet.valide",
            type_objet="Reservation",
            id_objet=reservation["reservation_id"],
            recepteurs=["quiz"],
            date=date,
            modifications=[
                {"donnee": "billets", "operation": "remplacer", "nouvelle_valeur": billets_publics(mise_a_jour["billets"])}
            ],
            contexte={
                "billet_id": billet_id,
                "titulaire_id": billet["titulaire_id"],
                "seance_id": seance["seance_id"],
                "film_id": seance["film_id"],
                "version": seance["version"],
                "fin_seance": seance["fin"],
            },
        )
        return next(b for b in billets_publics(mise_a_jour["billets"]) if b["billet_id"] == billet_id)

    return executer_transaction(request, transaction)


@routeur.get("/reservations/{reservation_id}")
def lire_reservation(
    reservation_id: str, request: Request, client_id: str = Depends(client_authentifie)
) -> dict[str, Any]:
    reservation = request.app.state.db.reservations.find_one({"reservation_id": reservation_id})
    if reservation is None or reservation["acheteur_id"] != client_id:
        raise HTTPException(404, detail="Réservation inconnue.")
    return vue_reservation(reservation)


@routeur.get("/clients/moi/reservations")
def mes_reservations(request: Request, client_id: str = Depends(client_authentifie)) -> list[dict[str, Any]]:
    return list(request.app.state.db.reservations_par_client.find({"acheteur_id": client_id}, {"_id": 0}))


@routeur.get("/clients/moi/billets")
def mes_billets(request: Request, client_id: str = Depends(client_authentifie)) -> list[dict[str, Any]]:
    return list(request.app.state.db.billets_par_client.find({"titulaire_id": client_id}, {"_id": 0}))


@routeur.get("/seances/{seance_id}")
def lire_seance(seance_id: str, request: Request) -> dict[str, Any]:
    seance = charger_seance(request, seance_id)
    seance.pop("version_ventes", None)
    return seance
