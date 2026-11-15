import threading
from contextlib import asynccontextmanager

from services.commun.application import creer_application
from services.commun.livraison import Livraison
from services.commun.publication import Publication
from services.reservations.commandes import routeur
from services.reservations.reception import MODE_DEV, ReceptionReservations, routeur_dev
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
        redis, db = application.state.redis, application.state.db
        arret = threading.Event()
        travailleurs = [
            threading.Thread(
                target=Publication(redis, BoiteEnvoiMongo(db), SERVICE).executer,
                args=(arret,),
                name="publication-reservations",
                daemon=True,
            ),
            threading.Thread(
                target=Livraison(redis, ReceptionReservations(db), SERVICE).executer,
                args=(arret,),
                name="livraison-reservations",
                daemon=True,
            ),
        ]
        for travailleur in travailleurs:
            travailleur.start()
        try:
            yield
        finally:
            arret.set()
            for travailleur in travailleurs:
                travailleur.join(timeout=5)


app.router.lifespan_context = cycle_de_vie
