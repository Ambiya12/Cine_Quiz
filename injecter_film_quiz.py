"""Depose dans evenements:quiz:ready un film de test, au format exact du Catalogue :
film.cree (sans questions), puis film.modifie (10 questions, operation "remplacer").
Fichier sans accents pour pouvoir etre envoye par PowerShell sans probleme d'encodage."""
import os
import uuid
from datetime import UTC, datetime

from redis import Redis

from services.commun.evenements import Evenement

film_id = f"film_{uuid.uuid4().hex[:8]}"
date = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
questions = [
    {
        "question_id": f"q{n}",
        "texte": f"Question de test numero {n} ?",
        "choix": [{"choix_id": c, "texte": f"Reponse {c.upper()}"} for c in "abcd"],
        "bonne_reponse_id": "b",
    }
    for n in range(1, 11)
]
commun = {
    "version_schema": 1,
    "date": date,
    "emetteur": "catalogue",
    "recepteurs": ["quiz", "recherche"],
    "type_objet": "Film",
    "id_objet": film_id,
}
messages = [
    {
        **commun,
        "id_evenement": f"evt_{uuid.uuid4().hex}",
        "sequence_objet": 1,
        "type": "film.cree",
        "donnees": {
            "titre": "Film de test",
            "synopsis": "",
            "genres": [],
            "duree_min": 120,
            "a_l_affiche": True,
            "questions": [],
        },
    },
    {
        **commun,
        "id_evenement": f"evt_{uuid.uuid4().hex}",
        "sequence_objet": 2,
        "type": "film.modifie",
        "modifications": [{"donnee": "questions", "operation": "remplacer", "nouvelle_valeur": questions}],
    },
]
redis = Redis.from_url(os.environ["REDIS_URL"])
for message in messages:
    evenement = Evenement.model_validate(message)  # meme controle que le Catalogue
    redis.rpush("evenements:quiz:ready", evenement.model_dump_json(exclude_unset=True))
print("Film de test depose pour le Quiz :", film_id)
