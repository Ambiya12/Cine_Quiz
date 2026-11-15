import threading
from contextlib import asynccontextmanager

from services.catalogue.boite_envoi import SERVICE, BoiteEnvoiCatalogue, creer_index_boite_envoi
from services.catalogue.films import router as routeur_films
from services.catalogue.salles import routeur as routeur_salles
from services.catalogue.seances import routeur as routeur_seances
from services.commun.application import creer_application
from services.commun.publication import Publication

app = creer_application("catalogue", utiliser_mongo=True)
app.include_router(routeur_films)
app.include_router(routeur_salles)
app.include_router(routeur_seances)

cycle_commun = app.router.lifespan_context

@asynccontextmanager
async def cycle_de_vie(application):
    async with cycle_commun(application):
        db = application.state.db
        db.films.create_index("film_id", unique=True)
        db.salles.create_index("salle_id", unique=True)
        db.seances.create_index("seance_id", unique=True)
        db.seances.create_index([("film_id", 1), ("debut", 1)])
        db.seances.create_index([("salle_id", 1), ("debut", 1)])
        creer_index_boite_envoi(db)

        # Worker de publication : envoie dans Redis les événements de la boîte d'envoi
        arret = threading.Event()
        publication = Publication(application.state.redis, BoiteEnvoiCatalogue(db), SERVICE)
        worker = threading.Thread(
            target=publication.executer, args=(arret,), name="publication-catalogue", daemon=True
        )
        worker.start()
        try:
            yield
        finally:
            # Arrêt propre : le worker termine le message en cours puis s'arrête
            arret.set()
            worker.join(timeout=5)

app.router.lifespan_context = cycle_de_vie