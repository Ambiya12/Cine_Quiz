import logging
import threading

from redis import Redis

from services.commun.evenements import BoiteEnvoi, Evenement

logger = logging.getLogger(__name__)


def cle_journal(emetteur: str) -> str:
    return f"evenements:{emetteur}:journal"


def cle_publies(emetteur: str) -> str:
    return f"evenements:{emetteur}:publies"


def cle_ready(recepteur: str) -> str:
    return f"evenements:{recepteur}:ready"


# KEYS[1] journal, KEYS[2] publies, KEYS[3..] ready des récepteurs
# ARGV[1] id_evenement, ARGV[2] message JSON
# Les types des clés sont vérifiés avant toute écriture. Un message déjà
# publié n'est ni rajouté au journal ni redistribué : le script renvoie
# l'identifiant de son entrée existante.
SCRIPT_PUBLICATION = """
local function type_invalide(cle, attendu)
  local type_cle = redis.call('TYPE', cle)['ok']
  return type_cle ~= 'none' and type_cle ~= attendu
end

if type_invalide(KEYS[1], 'stream') then
  return redis.error_reply('WRONGTYPE ' .. KEYS[1] .. ' doit être un stream')
end
if type_invalide(KEYS[2], 'hash') then
  return redis.error_reply('WRONGTYPE ' .. KEYS[2] .. ' doit être un hash')
end
for i = 3, #KEYS do
  if type_invalide(KEYS[i], 'list') then
    return redis.error_reply('WRONGTYPE ' .. KEYS[i] .. ' doit être une list')
  end
end

local existant = redis.call('HGET', KEYS[2], ARGV[1])
if existant then
  local entrees = redis.call('XRANGE', KEYS[1], existant, existant)
  if #entrees == 1 then
    local champs = entrees[1][2]
    for j = 1, #champs, 2 do
      if champs[j] == 'message' then
        if champs[j + 1] ~= ARGV[2] then
          return redis.error_reply('CONFLIT ' .. ARGV[1] .. ' déjà publié avec un autre contenu')
        end
        return existant
      end
    end
  end
  return redis.error_reply('INCOHERENCE ' .. ARGV[1] .. ' absent du journal ' .. KEYS[1])
end

local id_journal = redis.call('XADD', KEYS[1], '*', 'id_evenement', ARGV[1], 'message', ARGV[2])
redis.call('HSET', KEYS[2], ARGV[1], id_journal)
for i = 3, #KEYS do
  redis.call('RPUSH', KEYS[i], ARGV[2])
end
return id_journal
"""


class Publication:
    """Worker unique de publication des messages en attente d'un émetteur."""

    def __init__(self, redis: Redis, boite: BoiteEnvoi, emetteur: str, *, attente: float = 0.5):
        self._boite = boite
        self._emetteur = emetteur
        self._attente = attente
        self._script = redis.register_script(SCRIPT_PUBLICATION)

    def publier(self, message: str) -> str:
        """Ajouter le message au journal et à chaque file ready ; renvoyer son identifiant de journal."""
        return self._publier(Evenement.model_validate_json(message), message)

    def _publier(self, evenement: Evenement, message: str) -> str:
        if evenement.emetteur != self._emetteur:
            raise ValueError(
                f"{evenement.id_evenement} a pour émetteur {evenement.emetteur}, pas {self._emetteur}."
            )
        recepteurs = dict.fromkeys(evenement.recepteurs)
        cles = [
            cle_journal(self._emetteur),
            cle_publies(self._emetteur),
            *(cle_ready(recepteur) for recepteur in recepteurs),
        ]
        return self._script(keys=cles, args=[evenement.id_evenement, message])

    def publier_suivant(self) -> bool:
        """Publier puis confirmer le plus ancien message en attente ; False si la boîte est vide."""
        message = self._boite.prochain_message()
        if message is None:
            return False
        evenement = Evenement.model_validate_json(message)
        self._publier(evenement, message)
        self._boite.confirmer_publication(evenement.id_evenement)
        return True

    def executer(self, arret: threading.Event) -> None:
        """Publier dans l'ordre jusqu'à l'arrêt ; un échec est retenté avec le même message."""
        while not arret.is_set():
            try:
                if self.publier_suivant():
                    continue
            except Exception:
                logger.exception("Échec de publication pour %s, nouvel essai", self._emetteur)
            arret.wait(self._attente)
