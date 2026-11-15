"""Adaptateur Redis du protocole Reception commun.

Le worker de livraison vérifie dans le journal les séquences intermédiaires
qui ne visaient pas Quiz avant d'appeler appliquer(). L'adaptateur accepte donc
une progression strictement croissante, sans supposer que les séquences sont
contiguës pour chaque destinataire.
"""
from __future__ import annotations

from redis import Redis

from services.commun.evenements import Evenement
from services.quiz.main import traiter_evenement

SAUVER_PROGRESS_LUA = r"""
local old = redis.call('HGET', KEYS[1], ARGV[1])
if old and old ~= ARGV[2] then
  return redis.error_reply('ID_EVENT_CONFLICT')
end
local current = tonumber(redis.call('GET', KEYS[2]) or '0')
local previous = tonumber(ARGV[4])
local sequence = tonumber(ARGV[3])
if current ~= previous then
  return redis.error_reply('PROGRESSION_CHANGED')
end
if sequence <= previous then
  return redis.error_reply('SEQUENCE_NOT_INCREASING')
end
redis.call('HSET', KEYS[1], ARGV[1], ARGV[2])
redis.call('SET', KEYS[2], sequence)
return 1
"""


class ReceptionRedis:
    def __init__(self, redis: Redis, prefix: str = "quiz") -> None:
        self.redis = redis
        self.prefix = prefix

    def message_traite(self, id_evenement: str) -> str | None:
        return self.redis.hget(f"{self.prefix}:messages_traites", id_evenement)

    def derniere_sequence(self, emetteur: str, type_objet: str, id_objet: str) -> int:
        cle = f"{self.prefix}:sequences_reception:{emetteur}:{type_objet}:{id_objet}"
        return int(self.redis.get(cle) or 0)

    def appliquer(self, evenement: Evenement, message: str, sequence_precedente: int) -> None:
        event = Evenement.model_validate_json(message)
        if event.id_evenement != evenement.id_evenement:
            raise ValueError("L'identifiant de l'événement ne correspond pas au message reçu")
        if event.sequence_objet <= sequence_precedente:
            raise ValueError("La séquence de l'événement doit dépasser la progression locale")

        cle_sequence = (
            f"{self.prefix}:sequences_reception:"
            f"{event.emetteur}:{event.type_objet}:{event.id_objet}"
        )
        courante = self.derniere_sequence(event.emetteur, event.type_objet, event.id_objet)
        if courante != sequence_precedente:
            raise ValueError("La progression locale a changé pendant le traitement")

        # Le worker commun doit avoir vérifié dans le journal que les éventuels
        # trous de séquence correspondent à des événements destinés à d'autres services.
        if self.prefix in event.recepteurs:
            traiter_evenement(self.redis, event.model_dump(mode="json", exclude_unset=True))

        # La sauvegarde est protégée par comparaison atomique de la progression.
        # Les effets métier possèdent leurs propres marqueurs Lua idempotents :
        # un rejeu après interruption ne répète pas les effets déjà appliqués.
        self.redis.eval(
            SAUVER_PROGRESS_LUA,
            2,
            f"{self.prefix}:messages_traites",
            cle_sequence,
            event.id_evenement,
            message,
            event.sequence_objet,
            sequence_precedente,
        )
