import threading
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Query, Request

from services.commun.application import creer_application
from services.commun.livraison import Livraison
from services.fournisseurs.reception import SERVICE, ReceptionFournisseurs

app = creer_application(SERVICE, utiliser_mongo=True)


def fournisseur_id_authentifie(x_fournisseur_id: Annotated[str, Header(min_length=1)]) -> str:
    """Identity supplied by the simulated authentication provider."""
    return x_fournisseur_id


@app.get("/consommations")
def lire_consommations(
    request: Request,
    fournisseur_id: str | None = None,
    date_de: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    date_a: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    fournisseur_id_authentifie: str = Depends(fournisseur_id_authentifie),
) -> list[dict]:
    if date_de and date_a and date_de > date_a:
        raise HTTPException(422, detail="date_de must be before or equal to date_a.")
    if fournisseur_id and fournisseur_id != fournisseur_id_authentifie:
        raise HTTPException(403, detail="Un fournisseur ne peut consulter que ses consommations.")
    filtre: dict[str, object] = {"fournisseur_id": fournisseur_id_authentifie}
    if date_de or date_a:
        filtre["date_locale"] = {**({"$gte": date_de} if date_de else {}), **({"$lte": date_a} if date_a else {})}
    return list(request.app.state.db.consommations.find(filtre, {"_id": 0}).sort([("fournisseur_id", 1), ("date_locale", 1)]))


@app.get("/consommations/{fournisseur_id}")
def lire_consommations_fournisseur(
    fournisseur_id: str, request: Request, fournisseur_id_authentifie: str = Depends(fournisseur_id_authentifie)
) -> list[dict]:
    if fournisseur_id != fournisseur_id_authentifie:
        raise HTTPException(403, detail="Un fournisseur ne peut consulter que ses consommations.")
    return list(
        request.app.state.db.consommations.find({"fournisseur_id": fournisseur_id}, {"_id": 0}).sort("date_locale", 1)
    )


cycle_commun = app.router.lifespan_context


@asynccontextmanager
async def cycle_de_vie(application):
    async with cycle_commun(application):
        arret = threading.Event()
        livraison = Livraison(application.state.redis, ReceptionFournisseurs(application.state.db), SERVICE)
        travailleur = threading.Thread(target=livraison.executer, args=(arret,), name="livraison-fournisseurs", daemon=True)
        travailleur.start()
        try:
            yield
        finally:
            arret.set()
            travailleur.join(timeout=5)


app.router.lifespan_context = cycle_de_vie
