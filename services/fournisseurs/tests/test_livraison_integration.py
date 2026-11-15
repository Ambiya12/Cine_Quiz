import json
import os
import unittest
from urllib.request import urlopen

from pymongo import MongoClient
from redis import Redis

from services.commun.livraison import (
    Livraison,
    cle_echecs,
    cle_essais,
    cle_journal,
    cle_processing,
    cle_ready,
)
from services.fournisseurs.reception import ReceptionFournisseurs


RECEPTEUR = "fournisseurs"


def message(*, identifiant: str, sequence: int, type: str = "coupon.utilise", recepteurs=None, date="2026-10-15T23:30:00Z"):
    return json.dumps(
        {
            "id_evenement": identifiant,
            "version_schema": 1,
            "sequence_objet": sequence,
            "type": type,
            "date": date,
            "emetteur": "quiz",
            "recepteurs": [RECEPTEUR] if recepteurs is None else recepteurs,
            "type_objet": "Coupon",
            "id_objet": "cpn_test",
            "modifications": [{"donnee": "utilise", "operation": "remplacer", "nouvelle_valeur": True}],
            "contexte": {
                "produit_choisi": "popcorn_moyen",
                "fournisseur_id": "fourn_popcorn_1",
                "film_origine_id": "film_test",
            },
        }
    )


class LivraisonIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.redis = Redis.from_url(
            os.environ["REDIS_URL"], decode_responses=True, socket_connect_timeout=2, socket_timeout=2
        )
        cls.mongo = MongoClient(os.environ["MONGO_URI"], serverSelectionTimeoutMS=2_000)
        cls.db = cls.mongo[os.environ["MONGO_DB"]]

    def setUp(self):
        self.db.client.drop_database(self.db.name)
        self.redis.delete(
            cle_ready(RECEPTEUR), cle_processing(RECEPTEUR), cle_essais(RECEPTEUR), cle_echecs(RECEPTEUR), cle_journal("quiz")
        )
        self.reception = ReceptionFournisseurs(self.db)
        self.livraison = Livraison(self.redis, self.reception, RECEPTEUR, attente=0)

    def pousser(self, contenu: str):
        self.redis.rpush(cle_ready(RECEPTEUR), contenu)

    def test_normal_delivery_and_projection(self):
        self.pousser(message(identifiant="evt_normal", sequence=1))

        self.assertTrue(self.livraison.livrer_suivant())

        consommation = self.db.consommations.find_one({"fournisseur_id": "fourn_popcorn_1"})
        self.assertEqual("2026-10-16", consommation["date_locale"])
        self.assertEqual(1, consommation["produits"]["popcorn_moyen"]["quantite"])
        self.assertEqual(1, consommation["quantite_totale"])
        self.assertEqual(0, self.redis.llen(cle_processing(RECEPTEUR)))

    def test_duplicate_event_does_not_increment_projection_twice(self):
        contenu = message(identifiant="evt_duplicate", sequence=1)
        self.pousser(contenu)
        self.pousser(contenu)

        self.livraison.livrer_suivant()
        self.livraison.livrer_suivant()

        consommation = self.db.consommations.find_one({"fournisseur_id": "fourn_popcorn_1"})
        self.assertEqual(1, consommation["quantite_totale"])
        self.assertEqual(0, self.redis.llen(cle_processing(RECEPTEUR)))

    def test_restart_recovers_message_left_in_processing(self):
        contenu = message(identifiant="evt_reprise", sequence=1)
        self.pousser(contenu)
        self.redis.lmove(cle_ready(RECEPTEUR), cle_processing(RECEPTEUR), "LEFT", "RIGHT")

        self.livraison.reprendre_processing()
        self.livraison.livrer_suivant()

        self.assertIsNotNone(self.reception.message_traite("evt_reprise"))
        self.assertEqual(0, self.redis.llen(cle_processing(RECEPTEUR)))

    def test_out_of_order_sequence_waits_for_missing_event(self):
        self.pousser(message(identifiant="evt_seq_2", sequence=2))

        self.livraison.livrer_suivant()

        self.assertIsNone(self.reception.message_traite("evt_seq_2"))
        self.assertEqual("1", self.redis.hget(cle_essais(RECEPTEUR), "evt_seq_2"))
        self.assertEqual(1, self.redis.llen(cle_processing(RECEPTEUR)))

    def test_sequence_two_is_processed_when_journal_shows_sequence_one_for_another_recipient(self):
        premier = message(identifiant="evt_autre", sequence=1, type="coupon.emis", recepteurs=["recherche"])
        second = message(identifiant="evt_seq_2", sequence=2)
        self.redis.xadd(cle_journal("quiz"), {"id_evenement": "evt_autre", "message": premier})
        self.pousser(second)

        self.livraison.livrer_suivant()

        self.assertIsNotNone(self.reception.message_traite("evt_autre"))
        self.assertIsNotNone(self.reception.message_traite("evt_seq_2"))
        self.assertEqual(2, self.reception.derniere_sequence("quiz", "Coupon", "cpn_test"))
        self.assertEqual(1, self.db.consommations.find_one()["quantite_totale"])

    def test_moves_message_to_failure_queue_after_five_attempts(self):
        self.pousser(message(identifiant="evt_failure", sequence=1, type="coupon.emis"))

        for _ in range(5):
            self.livraison.livrer_suivant()

        echec = json.loads(self.redis.lpop(cle_echecs(RECEPTEUR)))
        self.assertEqual("evt_failure", json.loads(echec["message"])["id_evenement"])
        self.assertEqual(5, echec["essais"])
        self.assertEqual(0, self.redis.llen(cle_processing(RECEPTEUR)))

    def test_health_and_consumption_endpoints(self):
        self.db.consommations.insert_one(
            {
                "_id": "fourn_popcorn_1|2026-10-16",
                "fournisseur_id": "fourn_popcorn_1",
                "date_locale": "2026-10-16",
                "fuseau_horaire": "Europe/Paris",
                "produits": {"popcorn_moyen": {"quantite": 1}},
                "quantite_totale": 1,
            }
        )

        with urlopen("http://127.0.0.1:8000/health") as reponse:
            self.assertEqual(200, reponse.status)
            self.assertEqual("ok", json.load(reponse)["statut"])
        with urlopen("http://127.0.0.1:8000/consommations/fourn_popcorn_1") as reponse:
            consommations = json.load(reponse)
        self.assertEqual("2026-10-16", consommations[0]["date_locale"])


if __name__ == "__main__":
    unittest.main()
