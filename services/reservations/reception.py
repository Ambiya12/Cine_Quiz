import json
import os
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request
from pymongo import ReturnDocument
from pymongo.client_session import ClientSession
from pymongo.database import Database

from services.commun.evenements import Evenement
from services.commun.publication import cle_journal
from services.reservations.stockage import SERVICE, annuler_reservation, date_iso, maintenant

CHAMPS_REPLICA = {"film_id", "titre_film", "salle_id", "debut", "debut_generique", "fin", "version", "tarifs", "plan", "statut"}


class ReceptionReservations:
    """Traitement des événements reçus par Réservations, compatible avec le protocole Reception."""

    def __init__(self, db: Database):
        self._db = db
        self._effets: dict[str, Callable[[ClientSession, Evenement], None]] = {
            "seance.creee": self._creer_replica,
            "seance.modifiee": self._modifier_replica,
        }

    def message_traite(self, id_evenement: str) -> str | None:
        document = self._db.evenements_traites.find_one({"_id": id_evenement})
        return document["message"] if document else None

    def derniere_sequence(self, emetteur: str, type_objet: str, id_objet: str) -> int:
        document = self._db.progression.find_one({"_id": f"{emetteur}:{type_objet}:{id_objet}"})
        return document["sequence"] if document else 0

    def appliquer(self, evenement: Evenement, message: str, sequence_precedente: int) -> None:
        if evenement.sequence_objet <= sequence_precedente:
            raise ValueError(f"{evenement.id_evenement} : séquence {evenement.sequence_objet} déjà dépassée.")
        concerne = SERVICE in evenement.recepteurs
        if concerne and evenement.type == "seance.annulee":
            self._annuler_seance(evenement, message, sequence_precedente)
            return
        if concerne and evenement.type not in self._effets:
            raise ValueError(f"Type d'événement non géré par {SERVICE} : {evenement.type}.")

        def transaction(session):
            self._verifier_progression(session, evenement, sequence_precedente)
            # Un fait adressé à un autre destinataire fait seulement avancer la progression.
            if concerne:
                self._effets[evenement.type](session, evenement)
            self._terminer(session, evenement, message)

        self._transaction(transaction)

    def _transaction(self, fonction):
        with self._db.client.start_session() as session:
            return session.with_transaction(fonction)

    def _verifier_progression(self, session: ClientSession, evenement: Evenement, sequence_precedente: int) -> None:
        document = self._db.progression.find_one(
            {"_id": f"{evenement.emetteur}:{evenement.type_objet}:{evenement.id_objet}"}, session=session
        )
        if (document["sequence"] if document else 0) != sequence_precedente:
            raise RuntimeError(f"La progression de {evenement.id_objet} a changé pendant le traitement.")

    def _terminer(self, session: ClientSession, evenement: Evenement, message: str) -> None:
        self._db.evenements_traites.insert_one({"_id": evenement.id_evenement, "message": message}, session=session)
        self._db.progression.update_one(
            {"_id": f"{evenement.emetteur}:{evenement.type_objet}:{evenement.id_objet}"},
            {"$set": {"sequence": evenement.sequence_objet}},
            upsert=True,
            session=session,
        )

    def _creer_replica(self, session: ClientSession, evenement: Evenement) -> None:
        donnees, contexte = evenement.donnees, evenement.contexte or {}
        replica: dict[str, Any] = {
            "seance_id": evenement.id_objet,
            "titre_film": contexte.get("titre_film"),
            "plan": contexte["plan"],
            "version_ventes": 0,
        }
        replica.update({cle: valeur for cle, valeur in donnees.items() if cle in CHAMPS_REPLICA})
        self._db.seances_replica.insert_one(replica, session=session)

    def _modifier_replica(self, session: ClientSession, evenement: Evenement) -> None:
        remplacer: dict[str, Any] = {}
        ajouter: dict[str, Any] = {}
        for modification in evenement.modifications:
            if modification.donnee not in CHAMPS_REPLICA:
                continue
            if modification.operation == "remplacer":
                remplacer[modification.donnee] = modification.nouvelle_valeur
            else:
                ajouter[modification.donnee] = modification.nouvelle_valeur
        operations = {"$set": remplacer} if remplacer else {}
        if ajouter:
            operations["$push"] = ajouter
        if not operations:
            return
        resultat = self._db.seances_replica.update_one({"seance_id": evenement.id_objet}, operations, session=session)
        if resultat.matched_count == 0:
            raise LookupError(f"Séance {evenement.id_objet} absente du réplica.")

    def _annuler_seance(self, evenement: Evenement, message: str, sequence_precedente: int) -> None:
        """Annulation reprenable : fermer la séance, annuler chaque réservation, puis terminer."""
        db, seance_id = self._db, evenement.id_objet

        def fermer(session):
            self._verifier_progression(session, evenement, sequence_precedente)
            # Écrire le réplica entre en conflit avec les confirmations et validations en cours.
            seance = db.seances_replica.find_one_and_update(
                {"seance_id": seance_id},
                {"$set": {"statut": "annulee"}, "$inc": {"version_ventes": 1}},
                return_document=ReturnDocument.AFTER,
                session=session,
            )
            if seance is None:
                raise LookupError(f"Séance {seance_id} absente du réplica.")
            db.suivis_annulation.update_one(
                {"_id": evenement.id_evenement},
                {"$setOnInsert": {"seance_id": seance_id, "statut": "en_cours", "annulees": 0, "commence_le": date_iso(maintenant())}},
                upsert=True,
                session=session,
            )
            return seance

        seance = self._transaction(fermer)

        while reservation_ids := db.billets_par_seance.distinct("reservation_id", {"seance_id": seance_id}):
            for reservation_id in reservation_ids:

                def annuler(session, reservation_id=reservation_id):
                    reservation = db.reservations.find_one({"reservation_id": reservation_id}, session=session)
                    if reservation and annuler_reservation(
                        db, session, reservation, seance, motif="annulation_cinema", date=maintenant()
                    ):
                        db.suivis_annulation.update_one(
                            {"_id": evenement.id_evenement}, {"$inc": {"annulees": 1}}, session=session
                        )
                    else:
                        db.billets_par_seance.delete_many({"reservation_id": reservation_id}, session=session)

                self._transaction(annuler)

        def terminer(session):
            self._verifier_progression(session, evenement, sequence_precedente)
            db.suivis_annulation.update_one(
                {"_id": evenement.id_evenement},
                {"$set": {"statut": "termine", "termine_le": date_iso(maintenant())}},
                session=session,
            )
            self._terminer(session, evenement, message)

        self._transaction(terminer)


# Outils de test : injection manuelle d'événements et lecture du journal publié.
routeur_dev = APIRouter(prefix="/dev", tags=["développement"])
MODE_DEV = os.environ.get("CINEQUIZ_DEV") == "1"


@routeur_dev.post("/evenements")
def recevoir_evenement(corps: dict[str, Any], request: Request) -> dict[str, Any]:
    """Accepte l'enveloppe seule ou un fichier d'exemple entier (clé evenement)."""
    message = json.dumps(corps.get("evenement", corps), ensure_ascii=False)
    evenement = Evenement.model_validate_json(message)
    reception = ReceptionReservations(request.app.state.db)
    if reception.message_traite(evenement.id_evenement) is not None:
        return {"id_evenement": evenement.id_evenement, "resultat": "deja_traite"}
    precedente = reception.derniere_sequence(evenement.emetteur, evenement.type_objet, evenement.id_objet)
    try:
        reception.appliquer(evenement, message, precedente)
    except (ValueError, LookupError, RuntimeError) as erreur:
        raise HTTPException(409, detail=str(erreur)) from erreur
    return {"id_evenement": evenement.id_evenement, "resultat": "traite"}


@routeur_dev.get("/journal")
def lire_journal(request: Request, type: str | None = None, id_objet: str | None = None) -> list[dict[str, Any]]:
    """Messages publiés dans evenements:reservations:journal, pour vérifier la publication."""
    messages = (
        json.loads(champs["message"])
        for _, champs in request.app.state.redis.xrange(cle_journal(SERVICE))
    )
    return [
        message
        for message in messages
        if (type is None or message["type"] == type) and (id_objet is None or message["id_objet"] == id_objet)
    ]
