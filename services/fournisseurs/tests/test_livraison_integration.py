import json
import os
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen
from urllib.request import Request as RequeteHttp

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


def configuration_test() -> tuple[str, str, str, str]:
    """Refuse any setup that could erase application data."""
    mongo_uri = os.environ.get("CINEQUIZ_TEST_MONGO_URI")
    mongo_db = os.environ.get("CINEQUIZ_TEST_MONGO_DB")
    redis_url = os.environ.get("CINEQUIZ_TEST_REDIS_URL")
    api_url = os.environ.get("CINEQUIZ_TEST_FOURNISSEURS_URL")
    if not all((mongo_uri, mongo_db, redis_url, api_url)):
        raise RuntimeError("CINEQUIZ_TEST_MONGO_URI, CINEQUIZ_TEST_MONGO_DB, CINEQUIZ_TEST_REDIS_URL and CINEQUIZ_TEST_FOURNISSEURS_URL are required.")
    if mongo_db == os.environ.get("MONGO_DB") or not mongo_db.startswith("cinequiz_test_"):
        raise RuntimeError("CINEQUIZ_TEST_MONGO_DB must be a dedicated cinequiz_test_* database.")
    if mongo_uri == os.environ.get("MONGO_URI") and mongo_db == os.environ.get("MONGO_DB"):
        raise RuntimeError("The test MongoDB configuration points to the application database.")
    verifier_redis_dedie(redis_url, os.environ.get("REDIS_URL", "redis://unused/0"))
    return mongo_uri, mongo_db, redis_url, api_url.rstrip("/")


def verifier_redis_dedie(redis_test_url: str, redis_application_url: str) -> None:
    """Compare the database actually selected by Redis-py, before deleting keys."""
    redis_test = Redis.from_url(redis_test_url)
    redis_application = Redis.from_url(redis_application_url)
    base_test = redis_test.connection_pool.connection_kwargs["db"]
    base_application = redis_application.connection_pool.connection_kwargs["db"]
    if base_test == base_application:
        raise RuntimeError("CINEQUIZ_TEST_REDIS_URL resolves to the application Redis database.")


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
        mongo_uri, mongo_db, redis_url, cls.api_url = configuration_test()
        cls.redis = Redis.from_url(
            redis_url, decode_responses=True, socket_connect_timeout=2, socket_timeout=2
        )
        cls.mongo = MongoClient(mongo_uri, serverSelectionTimeoutMS=2_000)
        cls.db = cls.mongo[mongo_db]

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

        with urlopen(f"{self.api_url}/health") as reponse:
            self.assertEqual(200, reponse.status)
            self.assertEqual("ok", json.load(reponse)["statut"])
        requete = RequeteHttp(
            f"{self.api_url}/consommations/fourn_popcorn_1", headers={"X-Fournisseur-Id": "fourn_popcorn_1"}
        )
        with urlopen(requete) as reponse:
            consommations = json.load(reponse)
        self.assertEqual("2026-10-16", consommations[0]["date_locale"])
        autre_fournisseur = RequeteHttp(
            f"{self.api_url}/consommations/fourn_boisson_1", headers={"X-Fournisseur-Id": "fourn_popcorn_1"}
        )
        with self.assertRaises(HTTPError) as erreur:
            urlopen(autre_fournisseur)
        self.assertEqual(403, erreur.exception.code)


if __name__ == "__main__":
    unittest.main()
