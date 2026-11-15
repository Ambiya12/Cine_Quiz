import uuid
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from pymongo import ASCENDING, ReturnDocument
from pymongo.client_session import ClientSession
from pymongo.database import Database

from services.commun.evenements import Evenement

SERVICE = "reservations"

INDEX = {
    "seances_replica": [([("seance_id", ASCENDING)], {"unique": True})],
    "reservations": [
        ([("reservation_id", ASCENDING)], {"unique": True}),
        ([("demande_id", ASCENDING)], {"unique": True}),
        ([("paiement_id", ASCENDING)], {"unique": True}),
    ],
    "billets_par_id": [([("billet_id", ASCENDING)], {"unique": True})],
    "reservations_par_client": [([("acheteur_id", ASCENDING), ("reservation_id", ASCENDING)], {"unique": True})],
    "billets_par_client": [([("titulaire_id", ASCENDING), ("billet_id", ASCENDING)], {"unique": True})],
    "billets_par_seance": [
        ([("seance_id", ASCENDING), ("place", ASCENDING)], {"unique": True}),
        ([("reservation_id", ASCENDING)], {}),
    ],
    "evenements_a_publier": [([("publie", ASCENDING), ("ordre", ASCENDING)], {})],
}


def creer_index(db: Database) -> None:
    for collection, index in INDEX.items():
        for cles, options in index:
            db[collection].create_index(cles, **options)


def maintenant() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def date_iso(valeur: datetime) -> str:
    return valeur.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def lire_date(valeur: str) -> datetime:
    return datetime.fromisoformat(valeur)


def billets_publics(billets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Billets publiés dans les événements, sans le jeton secret du QR code."""
    return [{cle: valeur for cle, valeur in billet.items() if cle != "jeton_qr"} for billet in billets]


def enregistrer_evenement(
    db: Database,
    session: ClientSession,
    *,
    type: str,
    type_objet: str,
    id_objet: str,
    recepteurs: list[str],
    date: datetime,
    donnees: dict[str, Any] | None = None,
    modifications: list[dict[str, Any]] | None = None,
    contexte: dict[str, Any] | None = None,
) -> str:
    """Réserver sequence_objet et mettre le message en attente, dans la transaction appelante."""
    compteur = db.sequences.find_one_and_update(
        {"_id": f"{SERVICE}:{type_objet}:{id_objet}"},
        {"$inc": {"valeur": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
        session=session,
    )
    champs: dict[str, Any] = {
        "id_evenement": f"evt_{uuid.uuid4().hex}",
        "version_schema": 1,
        "sequence_objet": compteur["valeur"],
        "type": type,
        "date": date_iso(date),
        "emetteur": SERVICE,
        "recepteurs": recepteurs,
        "type_objet": type_objet,
        "id_objet": id_objet,
    }
    if donnees is not None:
        champs["donnees"] = donnees
    if modifications is not None:
        champs["modifications"] = modifications
    if contexte is not None:
        champs["contexte"] = contexte
    evenement = Evenement.model_validate(champs)
    message = evenement.model_dump_json(exclude_unset=True)
    db.evenements_a_publier.insert_one(
        {"_id": evenement.id_evenement, "ordre": ObjectId(), "message": message, "publie": False},
        session=session,
    )
    return evenement.id_evenement


def annuler_reservation(
    db: Database,
    session: ClientSession,
    reservation: dict[str, Any],
    seance: dict[str, Any],
    *,
    motif: str,
    date: datetime,
) -> dict[str, Any] | None:
    """Annuler une réservation encore confirmee et la rembourser une seule fois.

    La mise à jour est conditionnée par le statut confirmee : une réservation déjà
    annulée, par l'acheteur ou par le cinéma, ne reçoit ni remboursement ni événement.
    """
    reservation_id = reservation["reservation_id"]
    billets = [{**billet, "statut": "annulee"} for billet in reservation["billets"]]
    remboursement = {
        "type": "remboursement",
        "montant": -reservation["montant"],
        "reference": f"rmb_{reservation_id}",
        "date": date_iso(date),
    }
    annulee = db.reservations.find_one_and_update(
        {"reservation_id": reservation_id, "statut": "confirmee"},
        {"$set": {"statut": "annulee", "billets": billets}, "$push": {"paiements": remboursement}},
        return_document=ReturnDocument.AFTER,
        session=session,
    )
    if annulee is None:
        return None
    # Libère seulement les places encore attribuées à cette réservation.
    db.billets_par_seance.delete_many({"reservation_id": reservation_id}, session=session)
    db.reservations_par_client.update_one(
        {"acheteur_id": reservation["acheteur_id"], "reservation_id": reservation_id},
        {"$set": {"statut": "annulee"}},
        session=session,
    )
    for billet in billets:
        db.billets_par_client.update_one(
            {"titulaire_id": billet["titulaire_id"], "billet_id": billet["billet_id"]},
            {"$set": {"statut": "annulee"}},
            session=session,
        )
    enregistrer_evenement(
        db,
        session,
        type="reservation.annulee",
        type_objet="Reservation",
        id_objet=reservation_id,
        recepteurs=[],
        date=date,
        modifications=[
            {"donnee": "statut", "operation": "remplacer", "nouvelle_valeur": "annulee"},
            {"donnee": "billets", "operation": "remplacer", "nouvelle_valeur": billets_publics(billets)},
            {"donnee": "paiements", "operation": "ajouter", "nouvelle_valeur": remboursement},
        ],
        contexte={
            "acheteur_id": reservation["acheteur_id"],
            "seance_id": reservation["seance_id"],
            "film_id": seance["film_id"],
            "motif": motif,
        },
    )
    return annulee


class BoiteEnvoiMongo:
    """Boîte d'envoi de Réservations, lue par le worker de publication."""

    def __init__(self, db: Database):
        self._db = db

    def prochain_message(self) -> str | None:
        document = self._db.evenements_a_publier.find_one({"publie": False}, sort=[("ordre", ASCENDING)])
        return document["message"] if document else None

    def confirmer_publication(self, id_evenement: str) -> None:
        self._db.evenements_a_publier.update_one(
            {"_id": id_evenement}, {"$set": {"publie": True, "publie_le": date_iso(maintenant())}}
        )
