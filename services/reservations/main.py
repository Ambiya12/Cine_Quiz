import threading
from contextlib import asynccontextmanager

from services.commun.application import creer_application
from services.commun.publication import Publication
from services.reservations.commandes import routeur
from services.reservations.reception import MODE_DEV, routeur_dev
from services.reservations.stockage import SERVICE, BoiteEnvoiMongo, creer_index

app = creer_application(SERVICE, utiliser_mongo=True)
app.include_router(routeur)
if MODE_DEV:
    app.include_router(routeur_dev)

cycle_commun = app.router.lifespan_context


@asynccontextmanager
async def cycle_de_vie(application):
    async with cycle_commun(application):
        creer_index(application.state.db)
        arret = threading.Event()
        publication = Publication(application.state.redis, BoiteEnvoiMongo(application.state.db), SERVICE)
        travailleur = threading.Thread(target=publication.executer, args=(arret,), name="publication", daemon=True)
        travailleur.start()
        try:
            yield
        finally:
            arret.set()
            travailleur.join(timeout=5)


app.router.lifespan_context = cycle_de_vie
