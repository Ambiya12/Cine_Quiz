"""Données de démonstration du Catalogue : 3 films, 10 questions chacun.

Le script passe par l'API (et non directement par MongoDB) : chaque film et chaque
question produisent donc leurs événements film.cree et film.modifie, comme en vrai.

Utilisation, à la racine du dépôt, une fois le service catalogue démarré :
    python scripts/donnees_demo.py
À lancer une seule fois sur une base vide : le relancer crée de nouveaux films en double.
"""
import json
import sys
import urllib.error
import urllib.request

CATALOGUE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8081"
EN_TETES = {"Content-Type": "application/json", "X-Role": "gerant"}


def envoyer(chemin: str, corps: dict) -> dict:
    """Envoie un POST au Catalogue et renvoie la réponse JSON ; s'arrête à la première erreur."""
    requete = urllib.request.Request(
        CATALOGUE + chemin, data=json.dumps(corps).encode(), headers=EN_TETES, method="POST"
    )
    try:
        with urllib.request.urlopen(requete) as reponse:
            return json.load(reponse)
    except urllib.error.HTTPError as erreur:
        sys.exit(f"Erreur {erreur.code} sur {chemin} : {erreur.read().decode()}")
    except urllib.error.URLError as erreur:
        sys.exit(f"Catalogue injoignable à {CATALOGUE} : {erreur.reason}")


def question(texte: str, choix: list[str], bonne: str) -> dict:
    """Construit une question : les 4 choix reçoivent les choix_id a, b, c, d."""
    return {
        "texte": texte,
        "choix": [{"choix_id": lettre, "texte": valeur} for lettre, valeur in zip("abcd", choix)],
        "bonne_reponse_id": bonne,
    }


FILMS = [
    {
        "film": {
            "titre": "Dune : Deuxième partie",
            "synopsis": "Paul Atreides s'allie aux Fremen pour venger sa famille.",
            "genres": ["science-fiction", "aventure"],
            "duree_min": 166,
            "debut_generique_min": 158,
            "casting": [
                {"acteur": "Timothée Chalamet", "role": "Paul Atreides"},
                {"acteur": "Zendaya", "role": "Chani"},
                {"acteur": "Rebecca Ferguson", "role": "Dame Jessica"},
                {"acteur": "Austin Butler", "role": "Feyd-Rautha"},
            ],
        },
        "questions": [
            question("Quelle est la couleur des yeux des Fremen ?", ["Vert", "Bleu", "Rouge", "Jaune"], "b"),
            question("Qui a réalisé le film ?", ["Ridley Scott", "Christopher Nolan", "Denis Villeneuve", "James Cameron"], "c"),
            question("Quel acteur joue Paul Atreides ?", ["Timothée Chalamet", "Austin Butler", "Tom Holland", "Oscar Isaac"], "a"),
            question("Quelle actrice joue Chani ?", ["Florence Pugh", "Zendaya", "Anya Taylor-Joy", "Rebecca Ferguson"], "b"),
            question("Sur quelle planète se déroule l'essentiel du film ?", ["Caladan", "Giedi Prime", "Kaitain", "Arrakis"], "d"),
            question("Quelle ressource précieuse est récoltée dans le désert ?", ["L'épice", "L'or", "L'eau lourde", "Le cristal"], "a"),
            question("Quelle créature géante vit sous le sable ?", ["Un dragon", "Un ver des sables", "Un kraken", "Un scorpion géant"], "b"),
            question("Quel acteur joue Feyd-Rautha Harkonnen ?", ["Dave Bautista", "Javier Bardem", "Austin Butler", "Stellan Skarsgård"], "c"),
            question("Qui a écrit le roman dont le film est adapté ?", ["Isaac Asimov", "Frank Herbert", "Arthur C. Clarke", "Philip K. Dick"], "b"),
            question("Qui a composé la musique du film ?", ["John Williams", "Ennio Morricone", "Alexandre Desplat", "Hans Zimmer"], "d"),
        ],
    },
    {
        "film": {
            "titre": "Le Roi Lion",
            "synopsis": "Le jeune lion Simba doit reprendre sa place de roi face à son oncle Scar.",
            "genres": ["animation", "aventure"],
            "duree_min": 88,
            "debut_generique_min": 82,
            "casting": [],
        },
        "questions": [
            question("Comment s'appelle le père de Simba ?", ["Mufasa", "Scar", "Rafiki", "Zazu"], "a"),
            question("Comment s'appelle l'oncle de Simba ?", ["Pumbaa", "Scar", "Timon", "Banzai"], "b"),
            question("Quel animal est Timon ?", ["Un phacochère", "Un mandrill", "Un suricate", "Un calao"], "c"),
            question("Quel animal est Pumbaa ?", ["Un hippopotame", "Un phacochère", "Un rhinocéros", "Un éléphant"], "b"),
            question("Quelle expression signifie « pas de soucis » dans le film ?", ["Hakuna Matata", "Asante Sana", "Jambo Bwana", "Circle of Life"], "a"),
            question("Quel animal est Rafiki ?", ["Un lion", "Une hyène", "Un suricate", "Un mandrill"], "d"),
            question("Comment s'appelle l'amie d'enfance de Simba ?", ["Sarabi", "Nala", "Shenzi", "Kiara"], "b"),
            question("Quel oiseau est le conseiller du roi ?", ["Zazu", "Iago", "Rafiki", "Ed"], "a"),
            question("En quelle année le film est-il sorti ?", ["1989", "1991", "1994", "1998"], "c"),
            question("Qui a composé les chansons du film avec Tim Rice ?", ["Phil Collins", "Elton John", "Alan Menken", "Stevie Wonder"], "b"),
        ],
    },
    {
        "film": {
            "titre": "Le Fabuleux Destin d'Amélie Poulain",
            "synopsis": "Une jeune serveuse de Montmartre décide de faire le bonheur des autres.",
            "genres": ["comédie", "romance"],
            "duree_min": 122,
            "debut_generique_min": 116,
            "casting": [
                {"acteur": "Audrey Tautou", "role": "Amélie Poulain"},
                {"acteur": "Mathieu Kassovitz", "role": "Nino Quincampoix"},
            ],
        },
        "questions": [
            question("Qui a réalisé le film ?", ["Luc Besson", "Jean-Pierre Jeunet", "François Ozon", "Cédric Klapisch"], "b"),
            question("Quelle actrice joue Amélie ?", ["Audrey Tautou", "Marion Cotillard", "Juliette Binoche", "Mélanie Laurent"], "a"),
            question("Dans quel quartier de Paris vit Amélie ?", ["Le Marais", "Belleville", "Montmartre", "Saint-Germain-des-Prés"], "c"),
            question("Comment s'appelle le café où travaille Amélie ?", ["Le Café de Flore", "Les Deux Moulins", "Les Deux Magots", "Le Procope"], "b"),
            question("Que collectionne Nino ?", ["Des timbres", "Des cartes postales", "Des billets de métro", "Des photos de photomaton déchirées"], "d"),
            question("Quel objet du père d'Amélie fait le tour du monde ?", ["Son nain de jardin", "Sa valise", "Son appareil photo", "Sa montre"], "a"),
            question("Qu'aime faire Amélie avec une petite cuillère ?", ["Remuer son café", "Casser la croûte de la crème brûlée", "Manger de la confiture", "Jouer de la musique"], "b"),
            question("Que trouve Amélie cachée dans le mur de sa salle de bain ?", ["Une lettre d'amour", "Une clé", "Une boîte à souvenirs d'enfant", "Un billet de banque"], "c"),
            question("Qui a composé la musique du film ?", ["Michel Legrand", "Vladimir Cosma", "Alexandre Desplat", "Yann Tiersen"], "d"),
            question("Quel acteur joue Nino ?", ["Mathieu Kassovitz", "Jamel Debbouze", "Vincent Cassel", "Guillaume Canet"], "a"),
        ],
    },
]


def main() -> None:
    for entree in FILMS:
        film = envoyer("/films", entree["film"])
        film_id = film["film_id"]
        for q in entree["questions"]:
            film = envoyer(f"/films/{film_id}/questions", q)
        print(f"{film_id} : {film['titre']} ({len(film['questions'])} questions)")


if __name__ == "__main__":
    main()
