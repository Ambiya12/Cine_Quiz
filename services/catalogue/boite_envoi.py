"""Boîte d'envoi du Catalogue (l'outbox du module 12 du cours).

Les routes enregistrent leurs événements ici, dans la même transaction MongoDB
que leurs modifications. Le worker de publication les lit ensuite grâce à
BoiteEnvoiCatalogue.
"""
import uuid
from datetime import datetime, timezone

from pymongo import ASCENDING, ReturnDocument
from pymongo.client_session import ClientSession
from pymongo.collection import Collection
from pymongo.database import Database

from services.commun.evenements import Evenement

COLLECTION = "evenements_a_publier"


def generer_identifiant(prefixe: str) -> str:
    """Renvoie un identifiant lisible et unique, par exemple film_3f9a1c2e."""
    return f"{prefixe}_{uuid.uuid4().hex[:8]}"


def reserver_sequence(collection: Collection, filtre: dict, session: ClientSession) -> int:
    """Augmente de 1 le compteur sequence_evenements de l'objet, et renvoie le nouveau numéro.

    À appeler dans la même transaction que l'enregistrement de l'événement.
    """
    document = collection.find_one_and_update(
        filtre,
        {"$inc": {"sequence_evenements": 1}},
        projection={"sequence_evenements": True},
        return_document=ReturnDocument.AFTER,
        session=session,
    )
    if document is None:
        raise LookupError("Objet introuvable : impossible de réserver une séquence.")
    return document["sequence_evenements"]


def enregistrer_evenement(
    db: Database,
    session: ClientSession,
    *,
    type_evenement: str,
    type_objet: str,
    id_objet: str,
    sequence_objet: int,
    recepteurs: list[str],
    donnees: dict | None = None,
    modifications: list[dict] | None = None,
    contexte: dict | None = None,
) -> str:
    """Vérifie l'événement, puis le range dans la boîte d'envoi, dans la transaction en cours.

    Renvoie l'identifiant de l'événement.
    """
    champs = {
        "id_evenement": f"evt_{uuid.uuid4().hex}",
        "version_schema": 1,
        "sequence_objet": sequence_objet,
        "type": type_evenement,
        "date": datetime.now(timezone.utc),
        "emetteur": "catalogue",
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
    evenement = Evenement(**champs)

    db[COLLECTION].insert_one(
        {
            "_id": evenement.id_evenement,
            "message": evenement.model_dump_json(exclude_unset=True),
            "publie": False,
            "cree_le": evenement.date,
        },
        session=session,
    )
    return evenement.id_evenement


def creer_index_boite_envoi(db: Database) -> None:
    """Index qui permet au worker de trouver rapidement le plus ancien message en attente."""
    db[COLLECTION].create_index([("publie", ASCENDING), ("cree_le", ASCENDING)])


class BoiteEnvoiCatalogue:
    """Implémente le protocole BoiteEnvoi du module commun, pour le worker de publication."""

    def __init__(self, db: Database) -> None:
        self._collection = db[COLLECTION]

    def prochain_message(self) -> str | None:
        """Renvoie le plus ancien message non publié, sans le retirer, ou None."""
        document = self._collection.find_one(
            {"publie": False},
            sort=[("cree_le", ASCENDING)],
        )
        return None if document is None else document["message"]

    def confirmer_publication(self, id_evenement: str) -> None:
        """Marque le message comme publié, une fois l'envoi dans Redis réussi."""
        self._collection.update_one(
            {"_id": id_evenement},
            {"$set": {"publie": True, "publie_le": datetime.now(timezone.utc)}},
        )