from datetime import datetime, timedelta
from typing import Annotated, Literal, Protocol

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    field_validator,
    model_validator,
)

Identifiant = Annotated[str, StringConstraints(min_length=1)]


class Modification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    donnee: Annotated[str, StringConstraints(pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")]
    operation: Literal["remplacer", "ajouter"]
    nouvelle_valeur: JsonValue


class Evenement(BaseModel):
    """Enveloppe publiée, sans la section de documentation des réactions."""

    model_config = ConfigDict(extra="forbid")

    id_evenement: Annotated[str, StringConstraints(pattern=r"^evt_.+$")]
    version_schema: Annotated[int, Field(strict=True, ge=1, le=1)]
    sequence_objet: Annotated[int, Field(strict=True, gt=0)]
    type: Annotated[str, StringConstraints(pattern=r"^[a-z_]+\.[a-z_]+$")]
    date: AwareDatetime
    emetteur: Identifiant
    recepteurs: list[Identifiant]
    type_objet: Identifiant
    id_objet: Identifiant
    donnees: dict[str, JsonValue] | None = None
    modifications: list[Modification] | None = None
    contexte: dict[str, JsonValue] | None = None

    @field_validator("date", mode="before")
    @classmethod
    def format_date(cls, valeur: object) -> object:
        if not isinstance(valeur, (str, datetime)):
            raise ValueError("La date doit être un horodatage ISO 8601.")
        if isinstance(valeur, str):
            return datetime.fromisoformat(valeur)
        return valeur

    @field_validator("date")
    @classmethod
    def date_utc(cls, valeur: datetime) -> datetime:
        if valeur.utcoffset() != timedelta(0):
            raise ValueError("La date doit être en UTC.")
        return valeur

    @model_validator(mode="after")
    def verifier_charge_utile(self) -> "Evenement":
        champs = self.model_fields_set & {"donnees", "modifications"}
        if len(champs) != 1 or (self.donnees is None and self.modifications is None):
            raise ValueError("Fournir soit donnees, soit modifications.")
        return self


class BoiteEnvoi(Protocol):
    """Accès du worker de publication à la boîte d'envoi du service."""

    def prochain_message(self) -> str | None:
        """Lire le plus ancien message non publié, sans le retirer."""
        ...

    def confirmer_publication(self, id_evenement: str) -> None:
        """Confirmer le message après sa distribution dans Redis."""
        ...


class Reception(Protocol):
    """Accès du worker de livraison à l'état et au traitement locaux."""

    def message_traite(self, id_evenement: str) -> str | None:
        """Lire le message original associé à un identifiant déjà traité."""
        ...

    def derniere_sequence(self, emetteur: str, type_objet: str, id_objet: str) -> int:
        """Lire la progression durable de l'objet, ou 0 pour un objet inconnu."""
        ...

    def appliquer(self, evenement: Evenement, message: str, sequence_precedente: int) -> None:
        """Valider atomiquement l'effet, le message traité et la nouvelle progression."""
        ...
