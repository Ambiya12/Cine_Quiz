import unittest

from redis.exceptions import RedisError

from services.commun.livraison import Livraison, cle_processing, cle_ready


class ArretApresDeuxAttentes:
    def __init__(self):
        self.attentes = 0

    def is_set(self) -> bool:
        return self.attentes >= 2

    def wait(self, attente: float) -> None:
        self.attentes += 1


class ArretApresUneAttente(ArretApresDeuxAttentes):
    def is_set(self) -> bool:
        return self.attentes >= 1


class RedisIndisponiblePuisDisponible:
    def __init__(self):
        self.appels: list[tuple[str, str]] = []
        self.echec = True

    def register_script(self, script):
        return lambda **kwargs: 1

    def lmove(self, source: str, destination: str, gauche: str, droite: str):
        self.appels.append((source, destination))
        if (source, destination) == (cle_ready("fournisseurs"), cle_processing("fournisseurs")) and self.echec:
            self.echec = False
            raise RedisError("Redis temporarily unavailable")
        return None


class LivraisonResilienceTest(unittest.TestCase):
    def test_worker_retries_when_redis_is_temporarily_unavailable(self):
        redis = RedisIndisponiblePuisDisponible()
        arret = ArretApresDeuxAttentes()
        livraison = Livraison(redis, reception=object(), recepteur="fournisseurs", attente=0)

        livraison.executer(arret)

        prise = (cle_ready("fournisseurs"), cle_processing("fournisseurs"))
        reprise = (cle_processing("fournisseurs"), cle_ready("fournisseurs"))
        self.assertEqual(2, redis.appels.count(prise))
        self.assertEqual(2, redis.appels.count(reprise))
        self.assertEqual(2, arret.attentes)

    def test_redis_error_discards_stale_in_memory_message_before_recovery(self):
        redis = RedisIndisponiblePuisDisponible()
        redis.echec = True
        arret = ArretApresUneAttente()
        livraison = Livraison(redis, reception=object(), recepteur="fournisseurs", attente=0)
        livraison._message_en_cours = "message-before-disconnect"

        # The first failure occurs during processing recovery, before a message can
        # be delivered again. The stale in-memory pointer must not survive it.
        original_lmove = redis.lmove

        def redis_down_once(source, destination, gauche, droite):
            redis.lmove = original_lmove
            raise RedisError("Redis unavailable during acknowledgement")

        redis.lmove = redis_down_once
        livraison.executer(arret)

        self.assertIsNone(livraison._message_en_cours)


if __name__ == "__main__":
    unittest.main()
