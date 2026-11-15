import unittest

from services.fournisseurs.tests.test_livraison_integration import verifier_redis_dedie


class ConfigurationTest(unittest.TestCase):
    def test_different_urls_resolving_to_application_database_are_refused(self):
        with self.assertRaisesRegex(RuntimeError, "application Redis database"):
            verifier_redis_dedie("redis://localhost:6379/15?db=0", "redis://localhost:6379/0")

    def test_dedicated_redis_database_is_accepted(self):
        verifier_redis_dedie("redis://localhost:6379/15", "redis://localhost:6379/0")


if __name__ == "__main__":
    unittest.main()
