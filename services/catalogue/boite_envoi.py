"""Boîte d'envoi du Catalogue (l'outbox du module 12 du cours).

Même fonctionnement que celle de Réservations (services/reservations/stockage.py) :
- enregistrer_evenement réserve le numéro de séquence et range le message,
  dans la transaction de l'appelant ;
- BoiteEnvoiCatalogue implémente le protocole BoiteEnvoi, lu par le worker de publication.
"""
import uuid
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from pymongo import ASCENDING, ReturnDocument
from pymongo.client_session import ClientSession
from pymongo.database import Database

from services.commun.evenements import Evenement

SERVICE = "catalogue"


def generer_identifiant(prefixe: str) -> str:
    """Renvoie un identifiant lisible et unique, par exemple film_3f9a1c2e."""
    return f"{prefixe}_{uuid.uuid4().hex[:8]}"


def maintenant() -> datetime:
    """L'heure actuelle en UTC, à la seconde près."""
    return datetime.now(UTC).replace(microsecond=0)


def date_iso(valeur: datetime) -> str:
    """Une date au format des événements, par exemple 2026-10-07T20:00:00Z."""
    return valeur.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


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
    """Réserve sequence_objet et met le message en attente, dans la transaction de l'appelant."""
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

    # Le modèle commun refuse tout événement qui ne respecte pas le contrat
    evenement = Evenement.model_validate(champs)
    db.evenements_a_publier.insert_one(
        {
            "_id": evenement.id_evenement,
            "ordre": ObjectId(),
            "message": evenement.model_dump_json(exclude_unset=True),
            "publie": False,
        },
        session=session,
    )
    return evenement.id_evenement


def creer_index_boite_envoi(db: Database) -> None:
    """Index qui permet au worker de trouver rapidement le plus ancien message en attente."""
    db.evenements_a_publier.create_index([("publie", ASCENDING), ("ordre", ASCENDING)])


class BoiteEnvoiCatalogue:
    """Implémente le protocole BoiteEnvoi du module commun, pour le worker de publication."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def prochain_message(self) -> str | None:
        """Renvoie le plus ancien message non publié, sans le retirer, ou None."""
        document = self._db.evenements_a_publier.find_one(
            {"publie": False}, sort=[("ordre", ASCENDING)]
        )
        return document["message"] if document else None

    def confirmer_publication(self, id_evenement: str) -> None:
        """Marque le message comme publié, une fois l'envoi dans Redis réussi."""
        self._db.evenements_a_publier.update_one(
            {"_id": id_evenement},
            {"$set": {"publie": True, "publie_le": date_iso(maintenant())}},
        )