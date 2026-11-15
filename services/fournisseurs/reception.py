from zoneinfo import ZoneInfo

from pymongo.client_session import ClientSession
from pymongo.database import Database

from services.commun.evenements import Evenement

SERVICE = "fournisseurs"
FUSEAU_CINEMA = ZoneInfo("Europe/Paris")


class ReceptionFournisseurs:
    """MongoDB read projection for coupon usage, compatible with Reception."""

    def __init__(self, db: Database):
        self._db = db

    def message_traite(self, id_evenement: str) -> str | None:
        document = self._db.evenements_recus.find_one({"_id": id_evenement})
        return document["message"] if document else None

    def derniere_sequence(self, emetteur: str, type_objet: str, id_objet: str) -> int:
        document = self._db.progression.find_one({"_id": f"{emetteur}:{type_objet}:{id_objet}"})
        return document["sequence"] if document else 0

    def appliquer(self, evenement: Evenement, message: str, sequence_precedente: int) -> None:
        if evenement.sequence_objet <= sequence_precedente:
            raise ValueError(f"{evenement.id_evenement}: sequence already exceeded.")

        def transaction(session: ClientSession) -> None:
            self._verifier_progression(session, evenement, sequence_precedente)
            self._db.evenements_recus.insert_one(
                {"_id": evenement.id_evenement, "message": message, "evenement": evenement.model_dump(mode="json")},
                session=session,
            )
            if SERVICE in evenement.recepteurs:
                self._appliquer_coupon_utilise(session, evenement)
            self._db.progression.update_one(
                {"_id": self._cle_progression(evenement)},
                {"$set": {"sequence": evenement.sequence_objet}},
                upsert=True,
                session=session,
            )

        with self._db.client.start_session() as session:
            session.with_transaction(transaction)

    def _verifier_progression(self, session: ClientSession, evenement: Evenement, precedente: int) -> None:
        document = self._db.progression.find_one({"_id": self._cle_progression(evenement)}, session=session)
        if (document["sequence"] if document else 0) != precedente:
            raise RuntimeError(f"Progression changed for {evenement.id_objet} during processing.")

    def _appliquer_coupon_utilise(self, session: ClientSession, evenement: Evenement) -> None:
        if evenement.type != "coupon.utilise":
            raise ValueError(f"Unsupported event type for {SERVICE}: {evenement.type}.")
        contexte = evenement.contexte or {}
        fournisseur_id = contexte.get("fournisseur_id")
        produit = contexte.get("produit_choisi")
        if not isinstance(fournisseur_id, str) or not fournisseur_id:
            raise ValueError("coupon.utilise requires contexte.fournisseur_id.")
        if not isinstance(produit, str) or not produit or "." in produit or produit.startswith("$"):
            raise ValueError("coupon.utilise requires a valid contexte.produit_choisi.")
        date_locale = evenement.date.astimezone(FUSEAU_CINEMA).date().isoformat()
        self._db.consommations.update_one(
            {"_id": f"{fournisseur_id}|{date_locale}"},
            {
                "$setOnInsert": {
                    "fournisseur_id": fournisseur_id,
                    "date_locale": date_locale,
                    "fuseau_horaire": "Europe/Paris",
                },
                "$inc": {f"produits.{produit}.quantite": 1, "quantite_totale": 1},
            },
            upsert=True,
            session=session,
        )

    @staticmethod
    def _cle_progression(evenement: Evenement) -> str:
        return f"{evenement.emetteur}:{evenement.type_objet}:{evenement.id_objet}"
