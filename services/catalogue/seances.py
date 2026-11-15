from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal, Self

from bson.decimal128 import Decimal128
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)
from pymongo.client_session import ClientSession
from pymongo.database import Database

from services.catalogue.acces import gerant_authentifie
from services.catalogue.boite_envoi import SERVICE, enregistrer_evenement, generer_identifiant

routeur = APIRouter(prefix="/seances", tags=["séances"], dependencies=[Depends(gerant_authentifie)])

DUREE_NETTOYAGE = timedelta(minutes=15)
TYPE_OBJET = "Seance"
DESTINATAIRES = ("reservations", "recherche")
CHAMPS_HORAIRES = ("debut", "debut_generique", "fin", "fin_occupation")
Identifiant = Annotated[str, StringConstraints(min_length=1)]
TypeTarif = Literal["plein", "reduit", "enfant"]


def verifier_tarif_json(tarif: Decimal) -> Decimal:
    # Les événements transportent les tarifs sous forme de nombres JSON.
    if Decimal(str(float(tarif))) != tarif:
        raise ValueError("Le tarif doit être représentable sans perte dans le message JSON.")
    return tarif


Tarif = Annotated[Decimal, Field(gt=0, decimal_places=2), AfterValidator(verifier_tarif_json)]


class Tarifs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plein: Tarif
    reduit: Tarif
    enfant: Tarif

    @model_validator(mode="after")
    def verifier_ordre(self) -> Self:
        if not self.enfant <= self.reduit <= self.plein:
            raise ValueError("Les tarifs doivent respecter enfant ≤ reduit ≤ plein.")
        return self


class DemandeSeance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    film_id: Identifiant
    salle_id: Identifiant
    debut: AwareDatetime
    version: Literal["VF", "VOSTFR"]
    tarifs: Tarifs


class ModificationSeance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tarifs: Annotated[dict[TypeTarif, Tarif], Field(min_length=1)]


class DemandeAnnulation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    motif: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def date_utc(date: datetime) -> datetime:
    # PyMongo renvoie les dates BSON en UTC sans fuseau par défaut.
    return date.replace(tzinfo=UTC) if date.tzinfo is None else date.astimezone(UTC)


def tarifs_bson(tarifs: dict[str, Decimal]) -> dict[str, Decimal128]:
    return {nom: Decimal128(montant) for nom, montant in tarifs.items()}


def vue_seance(seance: dict) -> dict:
    vue = {champ: valeur for champ, valeur in seance.items() if champ not in {"_id", "sequence_evenements"}}
    for champ in CHAMPS_HORAIRES:
        vue[champ] = date_utc(seance[champ]).isoformat().replace("+00:00", "Z")
    vue["tarifs"] = {nom: float(montant.to_decimal()) for nom, montant in seance["tarifs"].items()}
    return vue


def charger_seance(db: Database, session: ClientSession, seance_id: str) -> dict:
    seance = db.seances.find_one({"seance_id": seance_id}, session=session)
    if seance is None:
        raise HTTPException(404, detail="Séance inconnue.")
    return seance


def reprendre_sequence(db: Database, session: ClientSession, seance: dict) -> None:
    """Reprend le compteur des séances enregistrées avant le compteur partagé."""
    sequence_precedente = seance.get("sequence_evenements")
    if sequence_precedente is not None:
        db.sequences.update_one(
            {"_id": f"{SERVICE}:{TYPE_OBJET}:{seance['seance_id']}"},
            {"$max": {"valeur": sequence_precedente}},
            upsert=True,
            session=session,
        )


@routeur.post("", status_code=201)
def creer_seance(demande: DemandeSeance, request: Request) -> dict:
    db = request.app.state.db
    seance_id = generer_identifiant("seance")
    try:
        debut = date_utc(demande.debut)
    except OverflowError as erreur:
        raise HTTPException(422, detail="La date de début est hors de la plage UTC.") from erreur
    # Utiliser la même précision que BSON pour le stockage et l'événement.
    debut = debut.replace(microsecond=debut.microsecond // 1000 * 1000)

    def transaction(session: ClientSession) -> dict:
        date = datetime.now(UTC)
        if debut <= date:
            raise HTTPException(422, detail="Le début de la séance doit être dans le futur.")

        # Sérialiser la programmation avec les changements du film qui écrivent ce document.
        film = db.films.find_one_and_update(
            {"film_id": demande.film_id}, {"$inc": {"version_programmation": 1}}, session=session
        )
        if film is None:
            raise HTTPException(404, detail="Film inconnu.")
        if not film["a_l_affiche"] or len(film["questions"]) < 10:
            raise HTTPException(409, detail="Le film doit être à l'affiche et disposer d'au moins 10 questions.")

        salle = db.salles.find_one_and_update(
            {"salle_id": demande.salle_id}, {"$inc": {"version_planning": 1}}, session=session
        )
        if salle is None:
            raise HTTPException(404, detail="Salle inconnue.")

        try:
            debut_generique = debut + timedelta(minutes=film["debut_generique_min"])
            fin = debut + timedelta(minutes=film["duree_min"])
            fin_occupation = fin + DUREE_NETTOYAGE
        except OverflowError as erreur:
            raise HTTPException(422, detail="Les horaires calculés sont hors de la plage de dates.") from erreur
        chevauchement = db.seances.find_one(
            {
                "salle_id": demande.salle_id,
                "statut": "programmee",
                "debut": {"$lt": fin_occupation},
                "fin_occupation": {"$gt": debut},
            },
            projection={"_id": 1},
            session=session,
        )
        if chevauchement is not None:
            raise HTTPException(409, detail="La salle est déjà occupée sur ce créneau.")

        seance = {
            "seance_id": seance_id,
            "film_id": demande.film_id,
            "salle_id": demande.salle_id,
            "debut": debut,
            "debut_generique": debut_generique,
            "fin": fin,
            "fin_occupation": fin_occupation,
            "version": demande.version,
            "tarifs": tarifs_bson(demande.tarifs.model_dump()),
            "statut": "programmee",
        }
        db.seances.insert_one(seance, session=session)
        vue = vue_seance(seance)
        enregistrer_evenement(
            db,
            session,
            type="seance.creee",
            type_objet=TYPE_OBJET,
            id_objet=seance_id,
            recepteurs=list(DESTINATAIRES),
            date=date,
            donnees={champ: valeur for champ, valeur in vue.items() if champ != "seance_id"},
            contexte={"titre_film": film["titre"], "plan": salle["plan"]},
        )
        return vue

    with db.client.start_session() as session:
        return session.with_transaction(transaction)


@routeur.patch("/{seance_id}")
def modifier_seance(seance_id: str, demande: ModificationSeance, request: Request) -> dict:
    db = request.app.state.db

    def transaction(session: ClientSession) -> dict:
        seance = charger_seance(db, session, seance_id)
        date = datetime.now(UTC)
        if seance["statut"] != "programmee" or date_utc(seance["debut"]) <= date:
            raise HTTPException(409, detail="Les tarifs ne sont modifiables qu'avant le début d'une séance programmée.")

        tarifs_actuels = {nom: montant.to_decimal() for nom, montant in seance["tarifs"].items()}
        try:
            tarifs = Tarifs.model_validate({**tarifs_actuels, **demande.tarifs}).model_dump()
        except ValidationError as erreur:
            raise HTTPException(422, detail="Les tarifs doivent respecter enfant ≤ reduit ≤ plein.") from erreur
        if tarifs == tarifs_actuels:
            return vue_seance(seance)

        seance["tarifs"] = tarifs_bson(tarifs)
        reprendre_sequence(db, session, seance)
        db.seances.update_one(
            {"seance_id": seance_id},
            {"$set": {"tarifs": seance["tarifs"]}, "$unset": {"sequence_evenements": ""}},
            session=session,
        )
        vue = vue_seance(seance)
        enregistrer_evenement(
            db,
            session,
            type="seance.modifiee",
            type_objet=TYPE_OBJET,
            id_objet=seance_id,
            recepteurs=list(DESTINATAIRES),
            date=date,
            modifications=[{"donnee": "tarifs", "operation": "remplacer", "nouvelle_valeur": vue["tarifs"]}],
            contexte={"film_id": seance["film_id"]},
        )
        return vue

    with db.client.start_session() as session:
        return session.with_transaction(transaction)


@routeur.post("/{seance_id}/annulation")
def annuler_seance(seance_id: str, demande: DemandeAnnulation, request: Request) -> dict:
    db = request.app.state.db

    def transaction(session: ClientSession) -> dict:
        seance = charger_seance(db, session, seance_id)
        if seance["statut"] == "annulee":
            return vue_seance(seance)
        date = datetime.now(UTC)
        if date_utc(seance["fin"]) <= date:
            raise HTTPException(409, detail="La séance est déjà terminée.")

        seance["statut"] = "annulee"
        reprendre_sequence(db, session, seance)
        db.seances.update_one(
            {"seance_id": seance_id},
            {"$set": {"statut": "annulee"}, "$unset": {"sequence_evenements": ""}},
            session=session,
        )
        enregistrer_evenement(
            db,
            session,
            type="seance.annulee",
            type_objet=TYPE_OBJET,
            id_objet=seance_id,
            recepteurs=["reservations", "quiz", "recherche"],
            date=date,
            modifications=[{"donnee": "statut", "operation": "remplacer", "nouvelle_valeur": "annulee"}],
            contexte={"film_id": seance["film_id"], "motif": demande.motif},
        )
        return vue_seance(seance)

    with db.client.start_session() as session:
        return session.with_transaction(transaction)
