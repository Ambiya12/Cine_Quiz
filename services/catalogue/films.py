"""Routes des films et de leurs questions : l'agrégat Film du Catalogue."""
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from pymongo.client_session import ClientSession
from pymongo.database import Database

from services.catalogue.acces import gerant_authentifie
from services.catalogue.boite_envoi import enregistrer_evenement, generer_identifiant, maintenant

# Toutes les routes des films sont réservées au gérant, comme celles des salles et des séances
router = APIRouter(prefix="/films", tags=["Films"], dependencies=[Depends(gerant_authentifie)])

# Destinataires des événements film.*, comme dans film.cree.json et film.modifie.json
DESTINATAIRES_FILM = ["quiz", "recherche"]
TYPE_OBJET = "Film"

# Champs techniques du document MongoDB, absents de l'agrégat décrit dans film.json
CHAMPS_TECHNIQUES = {"_id", "version_programmation"}

Texte = Annotated[str, Field(min_length=1)]
EntierPositif = Annotated[int, Field(strict=True, gt=0, le=2**63 - 1)]


# ---------------------------------------------------------------- Modèles des commandes


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


class Choix(BaseModel):
    model_config = ConfigDict(extra="forbid")

    choix_id: Texte
    texte: Texte


class QuestionAAjouter(BaseModel):
    """Une question envoyée par le gérant (commande AjouterQuestion)."""

    model_config = ConfigDict(extra="forbid")

    texte: Texte
    choix: list[Choix]
    bonne_reponse_id: Texte


class FilmAModifier(BaseModel):
    """Les champs que le gérant veut changer (commande ModifierFilm). Tous sont facultatifs."""

    model_config = ConfigDict(extra="forbid")

    titre: Texte = None
    synopsis: str = None
    genres: list[Texte] = None
    duree_min: EntierPositif = None
    debut_generique_min: EntierPositif = None
    casting: list[Role] = None


# ---------------------------------------------------------------- Fonctions communes


def vue_film(film: dict[str, Any]) -> dict[str, Any]:
    """Le film tel que l'API le montre : sans les champs techniques."""
    return {champ: valeur for champ, valeur in film.items() if champ not in CHAMPS_TECHNIQUES}


def charger_film(db: Database, session: ClientSession, film_id: str) -> dict[str, Any]:
    film = db.films.find_one({"film_id": film_id}, session=session)
    if film is None:
        raise HTTPException(404, "Film introuvable.")
    return film


def seance_a_venir(db: Database, session: ClientSession, film_id: str) -> bool:
    """Vrai s'il reste une séance programmée de ce film qui n'est pas encore terminée."""
    seance = db.seances.find_one(
        {"film_id": film_id, "statut": "programmee", "fin": {"$gt": datetime.now(UTC)}},
        projection={"_id": 1},
        session=session,
    )
    return seance is not None


def executer_transaction(db: Database, operation):
    """Exécute l'opération dans une transaction MongoDB et renvoie son résultat."""
    with db.client.start_session() as session:
        return session.with_transaction(operation)


# ---------------------------------------------------------------- Routes


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

    def transaction(session: ClientSession) -> None:
        # Les deux écritures réussissent ou échouent ensemble : c'est l'outbox
        db.films.insert_one(dict(document), session=session)
        enregistrer_evenement(
            db,
            session,
            type="film.cree",
            type_objet=TYPE_OBJET,
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

    executer_transaction(db, transaction)
    return document


@router.get("/{film_id}")
def lire_film(film_id: str, request: Request) -> dict:
    film = request.app.state.db.films.find_one({"film_id": film_id})
    if film is None:
        raise HTTPException(404, "Film introuvable.")
    return vue_film(film)


@router.post("/{film_id}/questions", status_code=201)
def ajouter_question(film_id: str, question: QuestionAAjouter, request: Request) -> dict:
    # Règles de film.json sur les questions : vérifiées avant d'ouvrir la transaction
    identifiants = [choix.choix_id for choix in question.choix]
    if len(identifiants) != 4:
        raise HTTPException(400, "Une question doit avoir exactement 4 choix.")
    if len(set(identifiants)) != 4:
        raise HTTPException(400, "Les choix_id d'une question doivent être tous différents.")
    if question.bonne_reponse_id not in identifiants:
        raise HTTPException(400, "bonne_reponse_id doit désigner l'un des 4 choix.")

    db = request.app.state.db

    def transaction(session: ClientSession) -> dict:
        film = charger_film(db, session, film_id)
        nouvelle_question = {
            # Les questions ne sont jamais supprimées : q1, q2, q3... restent uniques
            "question_id": f"q{len(film['questions']) + 1}",
            **question.model_dump(),
        }
        questions = film["questions"] + [nouvelle_question]
        db.films.update_one({"film_id": film_id}, {"$set": {"questions": questions}}, session=session)
        enregistrer_evenement(
            db,
            session,
            type="film.modifie",
            type_objet=TYPE_OBJET,
            id_objet=film_id,
            recepteurs=DESTINATAIRES_FILM,
            date=maintenant(),
            # Comme dans film.modifie.json : la liste complète remplace l'ancienne
            modifications=[{"donnee": "questions", "operation": "remplacer", "nouvelle_valeur": questions}],
        )
        film["questions"] = questions
        return vue_film(film)

    return executer_transaction(db, transaction)


@router.patch("/{film_id}")
def modifier_film(film_id: str, demande: FilmAModifier, request: Request) -> dict:
    changements_demandes = demande.model_dump(exclude_unset=True)
    if not changements_demandes:
        raise HTTPException(400, "Aucun champ à modifier.")

    db = request.app.state.db

    def transaction(session: ClientSession) -> dict:
        film = charger_film(db, session, film_id)
        # On ne garde que les champs dont la valeur change vraiment
        changements = {
            champ: valeur for champ, valeur in changements_demandes.items() if film.get(champ) != valeur
        }
        if not changements:
            return vue_film(film)

        duree = changements.get("duree_min", film["duree_min"])
        generique = changements.get("debut_generique_min", film["debut_generique_min"])
        if generique >= duree:
            raise HTTPException(400, "debut_generique_min doit être inférieur à duree_min.")

        # Règle de film.json : durée et générique figés tant qu'une séance programmée est à venir
        if {"duree_min", "debut_generique_min"} & changements.keys() and seance_a_venir(db, session, film_id):
            raise HTTPException(
                409, "Durée et générique ne changent pas tant qu'une séance programmée de ce film est à venir."
            )

        db.films.update_one({"film_id": film_id}, {"$set": changements}, session=session)
        enregistrer_evenement(
            db,
            session,
            type="film.modifie",
            type_objet=TYPE_OBJET,
            id_objet=film_id,
            recepteurs=DESTINATAIRES_FILM,
            date=maintenant(),
            modifications=[
                {"donnee": champ, "operation": "remplacer", "nouvelle_valeur": valeur}
                for champ, valeur in changements.items()
            ],
        )
        film.update(changements)
        return vue_film(film)

    return executer_transaction(db, transaction)


@router.post("/{film_id}/retrait-affiche")
def retirer_de_l_affiche(film_id: str, request: Request) -> dict:
    db = request.app.state.db

    def transaction(session: ClientSession) -> dict:
        film = charger_film(db, session, film_id)
        # Déjà retiré : rien ne change, aucun nouvel événement
        if not film["a_l_affiche"]:
            return vue_film(film)
        if seance_a_venir(db, session, film_id):
            raise HTTPException(409, "Ce film a encore une séance programmée à venir.")

        # Règle de film.json : le film retiré est conservé, jamais supprimé
        db.films.update_one({"film_id": film_id}, {"$set": {"a_l_affiche": False}}, session=session)
        enregistrer_evenement(
            db,
            session,
            type="film.modifie",
            type_objet=TYPE_OBJET,
            id_objet=film_id,
            recepteurs=DESTINATAIRES_FILM,
            date=maintenant(),
            modifications=[{"donnee": "a_l_affiche", "operation": "remplacer", "nouvelle_valeur": False}],
        )
        film["a_l_affiche"] = False
        return vue_film(film)

    return executer_transaction(db, transaction)