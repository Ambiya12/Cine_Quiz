import unittest

from fastapi import HTTPException

from services.fournisseurs.main import lire_consommations, lire_consommations_fournisseur


class AccesConsommationsTest(unittest.TestCase):
    def test_query_for_another_supplier_is_forbidden(self):
        with self.assertRaises(HTTPException) as erreur:
            lire_consommations(object(), "fourn_boisson_1", None, None, "fourn_popcorn_1")
        self.assertEqual(403, erreur.exception.status_code)

    def test_path_for_another_supplier_is_forbidden(self):
        with self.assertRaises(HTTPException) as erreur:
            lire_consommations_fournisseur("fourn_boisson_1", object(), "fourn_popcorn_1")
        self.assertEqual(403, erreur.exception.status_code)


if __name__ == "__main__":
    unittest.main()
