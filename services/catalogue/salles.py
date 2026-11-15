import re
from typing import Annotated, Self

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from services.catalogue.acces import gerant_authentifie
from services.catalogue.boite_envoi import generer_identifiant

routeur = APIRouter(prefix="/salles", tags=["salles"], dependencies=[Depends(gerant_authentifie)])

Rangee = Annotated[str, StringConstraints(pattern=r"^[A-Z]+$")]
EntierPositif = Annotated[int, Field(strict=True, gt=0, le=2**63 - 1)]
PLACE = re.compile(r"([A-Z]+)([1-9][0-9]*)")


class PlanSalle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rangees: Annotated[list[Rangee], Field(min_length=1)]
    places_par_rangee: EntierPositif
    places_pmr: list[str]

    @model_validator(mode="after")
    def verifier_plan(self) -> Self:
        rangees = set(self.rangees)
        if len(rangees) != len(self.rangees):
            raise ValueError("Les rangées doivent être distinctes.")
        if len(set(self.places_pmr)) != len(self.places_pmr):
            raise ValueError("Les places PMR doivent être distinctes.")

        longueur_numero = len(str(self.places_par_rangee))
        for place in self.places_pmr:
            correspondance = PLACE.fullmatch(place)
            if correspondance is None:
                raise ValueError(f"Place PMR invalide : {place}.")
            rangee, numero = correspondance.groups()
            if rangee not in rangees or len(numero) > longueur_numero or int(numero) > self.places_par_rangee:
                raise ValueError(f"La place PMR {place} ne figure pas dans le plan.")
        return self


class DemandeSalle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nom: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    capacite: EntierPositif
    plan: PlanSalle

    @model_validator(mode="after")
    def verifier_capacite(self) -> Self:
        if self.capacite != len(self.plan.rangees) * self.plan.places_par_rangee:
            raise ValueError("La capacité doit correspondre au nombre de places du plan.")
        return self


@routeur.post("", status_code=201)
def creer_salle(demande: DemandeSalle, request: Request) -> dict:
    salle = {"salle_id": generer_identifiant("salle"), **demande.model_dump(), "version_planning": 0}
    request.app.state.db.salles.insert_one(salle.copy())
    return salle
