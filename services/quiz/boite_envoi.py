"""Adaptateur Redis de la boîte d'envoi du service Quiz.

Compatible avec le protocole BoiteEnvoi défini dans services.commun.evenements.
Le message reste en tête de liste jusqu'à la confirmation du worker commun.
"""
from __future__ import annotations

import json
from redis import Redis

CONFIRMER_LUA = r"""
local raw = redis.call('LINDEX', KEYS[1], 0)
if not raw then return 0 end
local message = cjson.decode(raw)
if message.id_evenement ~= ARGV[1] then
  return redis.error_reply('OUTBOX_ORDER_CONFLICT')
end
redis.call('LPOP', KEYS[1])
return 1
"""


class BoiteEnvoiRedis:
    def __init__(self, redis: Redis, prefix: str = "quiz") -> None:
        self.redis = redis
        self.cle = f"{prefix}:evenements_a_publier"

    def prochain_message(self) -> str | None:
        return self.redis.lindex(self.cle, 0)

    def confirmer_publication(self, id_evenement: str) -> None:
        self.redis.eval(CONFIRMER_LUA, 1, self.cle, id_evenement)
