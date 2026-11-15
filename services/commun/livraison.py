"""Reliable delivery of events from the Redis transport queues."""

import hashlib
import logging
import threading

from redis import Redis
from redis.exceptions import RedisError

from services.commun.evenements import Evenement, Reception

logger = logging.getLogger(__name__)
MAX_ESSAIS = 5


def cle_ready(recepteur: str) -> str:
    return f"evenements:{recepteur}:ready"


def cle_processing(recepteur: str) -> str:
    return f"evenements:{recepteur}:processing"


def cle_essais(recepteur: str) -> str:
    return f"evenements:{recepteur}:essais"


def cle_echecs(recepteur: str) -> str:
    return f"evenements:{recepteur}:echecs"


def cle_journal(emetteur: str) -> str:
    return f"evenements:{emetteur}:journal"


# KEYS[1] essais (hash), KEYS[2] processing (list), KEYS[3] echecs (list)
# ARGV[1] identifiant de tentative, ARGV[2] message, ARGV[3] cause
SCRIPT_ECHEC = """
local function type_invalide(cle, attendu)
  local type_cle = redis.call('TYPE', cle)['ok']
  return type_cle ~= 'none' and type_cle ~= attendu
end

if type_invalide(KEYS[1], 'hash') then
  return redis.error_reply('WRONGTYPE ' .. KEYS[1] .. ' doit être un hash')
end
if type_invalide(KEYS[2], 'list') or type_invalide(KEYS[3], 'list') then
  return redis.error_reply('WRONGTYPE les files processing et echecs doivent être des lists')
end

local essais = redis.call('HINCRBY', KEYS[1], ARGV[1], 1)
if essais >= 5 then
  redis.call('RPUSH', KEYS[3], cjson.encode({message = ARGV[2], erreur = ARGV[3], essais = essais}))
  redis.call('LREM', KEYS[2], 1, ARGV[2])
  redis.call('HDEL', KEYS[1], ARGV[1])
end
return essais
"""


class Livraison:
    """Single-consumer worker for a recipient's ready/processing queues."""

    def __init__(self, redis: Redis, reception: Reception, recepteur: str, *, attente: float = 0.5):
        self._redis = redis
        self._reception = reception
        self._recepteur = recepteur
        self._attente = attente
        self._script_echec = redis.register_script(SCRIPT_ECHEC)
        self._message_en_cours: str | None = None
        self._dernier_essai_a_echoue = False

    def reprendre_processing(self) -> None:
        """Put work left by a stopped worker back at the front of ready, in FIFO order."""
        while self._redis.lmove(
            cle_processing(self._recepteur), cle_ready(self._recepteur), "RIGHT", "LEFT"
        ) is not None:
            pass

    def livrer_suivant(self) -> bool:
        """Deliver one message, returning False when no message is ready."""
        message = self._message_en_cours
        if message is None:
            message = self._redis.lmove(
                cle_ready(self._recepteur), cle_processing(self._recepteur), "LEFT", "RIGHT"
            )
            self._message_en_cours = message
        if message is None:
            return False
        try:
            evenement = Evenement.model_validate_json(message)
            identifiant_essais = evenement.id_evenement
            deja_traite = self._reception.message_traite(evenement.id_evenement)
            if deja_traite is None:
                precedente = self._completer_sequences(evenement)
                self._reception.appliquer(evenement, message, precedente)
            self._acquitter(message, identifiant_essais)
            self._message_en_cours = None
            self._dernier_essai_a_echoue = False
        except RedisError:
            # Infrastructure failures are recovered by executer(). They are not
            # business-processing attempts and must not consume the retry budget.
            raise
        except Exception as erreur:
            identifiant_essais = self._identifiant_essais(message)
            if self._enregistrer_echec(message, identifiant_essais, erreur) >= MAX_ESSAIS:
                self._message_en_cours = None
            self._dernier_essai_a_echoue = True
            logger.exception("Delivery failed for %s", self._recepteur)
        return True

    def executer(self, arret: threading.Event) -> None:
        """Recover interrupted work, then deliver messages until the stop event is set."""
        repris = False
        while not arret.is_set():
            try:
                if not repris:
                    self.reprendre_processing()
                    repris = True
                if self.livrer_suivant():
                    if self._dernier_essai_a_echoue:
                        arret.wait(self._attente)
                    continue
            except RedisError:
                # Re-run processing recovery after Redis reconnects: a command may
                # have succeeded server-side just before its connection was lost.
                repris = False
                self._message_en_cours = None
                logger.exception("Redis unavailable for %s, retrying", self._recepteur)
            arret.wait(self._attente)

    def _completer_sequences(self, evenement: Evenement) -> int:
        precedente = self._reception.derniere_sequence(
            evenement.emetteur, evenement.type_objet, evenement.id_objet
        )
        if evenement.sequence_objet <= precedente:
            raise ValueError(
                f"{evenement.id_evenement} : sequence {evenement.sequence_objet} already exceeded."
            )
        if evenement.sequence_objet == precedente + 1:
            return precedente

        manquantes = self._messages_manquants(evenement, precedente)
        for sequence in range(precedente + 1, evenement.sequence_objet):
            intermediaire, message = manquantes[sequence]
            if self._reception.message_traite(intermediaire.id_evenement) is None:
                # This also advances progression for facts addressed to another service.
                self._reception.appliquer(intermediaire, message, sequence - 1)
            precedente = sequence
        return precedente

    def _messages_manquants(self, evenement: Evenement, precedente: int) -> dict[int, tuple[Evenement, str]]:
        trou: dict[int, tuple[Evenement, str]] = {}
        for _, champs in self._redis.xrange(cle_journal(evenement.emetteur)):
            message = champs.get("message")
            if not isinstance(message, str):
                continue
            candidat = Evenement.model_validate_json(message)
            if (
                candidat.emetteur != evenement.emetteur
                or candidat.type_objet != evenement.type_objet
                or candidat.id_objet != evenement.id_objet
                or not precedente < candidat.sequence_objet < evenement.sequence_objet
            ):
                continue
            if candidat.sequence_objet in trou:
                raise ValueError(f"Duplicate sequence {candidat.sequence_objet} in sender journal.")
            trou[candidat.sequence_objet] = (candidat, message)

        attendues = set(range(precedente + 1, evenement.sequence_objet))
        if trou.keys() != attendues:
            manque = sorted(attendues - trou.keys())
            raise ValueError(f"Missing sequences in {cle_journal(evenement.emetteur)}: {manque}.")
        return trou

    def _acquitter(self, message: str, identifiant_essais: str) -> None:
        retire = self._redis.lrem(cle_processing(self._recepteur), 1, message)
        if retire != 1:
            raise RuntimeError("The processed message could not be acknowledged.")
        self._redis.hdel(cle_essais(self._recepteur), identifiant_essais)

    def _enregistrer_echec(self, message: str, identifiant_essais: str, erreur: Exception) -> int:
        essais = self._script_echec(
            keys=[cle_essais(self._recepteur), cle_processing(self._recepteur), cle_echecs(self._recepteur)],
            args=[identifiant_essais, message, str(erreur)],
        )
        if essais >= MAX_ESSAIS:
            logger.error("Message moved to the failure queue after %s attempts", essais)
        return essais

    @staticmethod
    def _identifiant_essais(message: str) -> str:
        try:
            return Evenement.model_validate_json(message).id_evenement
        except Exception:
            return f"invalide:{hashlib.sha256(message.encode()).hexdigest()}"
