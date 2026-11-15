"""Routes des films et de leurs questions : l'agrégat Film du Catalogue."""
from typing import Annotated

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from services.catalogue.boite_envoi import enregistrer_evenement, generer_identifiant, maintenant

router = APIRouter(prefix="/films", tags=["Films"])

# Destinataires des événements film.*, comme dans film.cree.json et film.modifie.json
DESTINATAIRES_FILM = ["quiz", "recherche"]

Texte = Annotated[str, Field(min_length=1)]
EntierPositif = Annotated[int, Field(strict=True, gt=0)]


class Role(BaseModel):
    model_config = ConfigDict(extra="forbid")

    acteur: Texte
    role: Texte


class FilmACreer(BaseModel):
    """Les données envoyées par le gérant pour créer un film (commande CreerFilm)."""

    model_config = ConfigDict(extra="forbid")

    titre: Texte
    synopsis: str = ""
    genres: list[Texte] = []
    duree_min: EntierPositif
    debut_generique_min: EntierPositif
    casting: list[Role] = []


@router.post("", status_code=201)
def creer_film(film: FilmACreer, request: Request) -> dict:
    # Règle de film.json : le générique commence avant la fin du film
    if film.debut_generique_min >= film.duree_min:
        raise HTTPException(400, "debut_generique_min doit être inférieur à duree_min.")

    db = request.app.state.db
    film_id = generer_identifiant("film")
    document = {
        "film_id": film_id,
        **film.model_dump(),
        "a_l_affiche": True,
        "questions": [],
    }

    def transaction(session) -> None:
        # Les deux écritures réussissent ou échouent ensemble : c'est l'outbox
        db.films.insert_one(dict(document), session=session)
        enregistrer_evenement(
            db,
            session,
            type="film.cree",
            type_objet="Film",
            id_objet=film_id,
            recepteurs=DESTINATAIRES_FILM,
            date=maintenant(),
            donnees={
                "titre": film.titre,
                "synopsis": film.synopsis,
                "genres": film.genres,
                "duree_min": film.duree_min,
                "a_l_affiche": True,
                "questions": [],
            },
        )

    with db.client.start_session() as session:
        session.with_transaction(transaction)

    return document


@router.get("/{film_id}")
def lire_film(film_id: str, request: Request) -> dict:
    film = request.app.state.db.films.find_one({"film_id": film_id}, {"_id": False})
    if film is None:
        raise HTTPException(404, "Film introuvable.")
    return film