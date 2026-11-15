from typing import Annotated

from fastapi import Header, HTTPException


def gerant_authentifie(x_role: Annotated[str, Header()] = "") -> None:
    """Contrôle le rôle fourni par le prestataire d'authentification simulé."""
    if x_role != "gerant":
        raise HTTPException(403, detail="Réservé aux gérants du cinéma.")
