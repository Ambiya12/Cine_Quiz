from services.catalogue import films
from services.commun.application import creer_application

app = creer_application("catalogue", utiliser_mongo=True)
app.include_router(films.router)
