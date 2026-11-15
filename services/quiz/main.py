"""Service Quiz et coupons CinéQuiz.

Les effets Redis qui engagent l'idempotence (réception, tentative, réponse,
récompense et utilisation d'un coupon) sont exécutés par scripts Lua.
Le worker de livraison commun appelle traiter_evenement(); le worker de
publication commun lit la liste Redis quiz:evenements_a_publier.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field
from redis import Redis
from redis.exceptions import RedisError
from pydantic import ValidationError
from services.commun.evenements import Evenement
from services.commun.publication import Publication
from services.quiz.boite_envoi import BoiteEnvoiRedis


UTC = timezone.utc
PARIS = ZoneInfo("Europe/Paris")
PREFIX = os.environ.get("REDIS_PREFIX", "quiz")
NB_QUESTIONS = 10
DUREE_QUESTION = 15
DELAI_DEMARRAGE = 2 * 60 * 60
DUREE_COUPON = 30 * 24 * 60 * 60
PRODUITS_ELIGIBLES = ("popcorn_moyen", "boisson")
FOURNISSEURS_PAR_PRODUIT = {"popcorn_moyen": "fourn_popcorn_1", "boisson": "fourn_boisson_1"}

# Toutes les validations de type et d'état sont faites avant les écritures.
RECEPTION_LUA = r"""
local old = redis.call('HGET', KEYS[1], ARGV[1])
if old then
  if old ~= ARGV[2] then return redis.error_reply('ID_EVENT_CONFLICT') end
  return 0
end
redis.call('HSET', KEYS[1], ARGV[1], ARGV[2])
return 1
"""

SYNC_FILM_LUA = r"""
local old = redis.call('HGET', KEYS[1], ARGV[1])
if old then if old ~= ARGV[2] then return redis.error_reply('ID_EVENT_CONFLICT') end return 0 end
redis.call('HSET', KEYS[2], 'titre', ARGV[3])
redis.call('DEL', KEYS[3])
local questions = cjson.decode(ARGV[4])
for _, q in ipairs(questions) do redis.call('HSET', KEYS[3], q.question_id, cjson.encode(q)) end
redis.call('HSET', KEYS[1], ARGV[1], ARGV[2])
return 1
"""

CREATE_ATTEMPT_LUA = r"""
local old_event = redis.call('HGET', KEYS[6], ARGV[5])
if old_event then if old_event ~= ARGV[6] then return redis.error_reply('ID_EVENT_CONFLICT') end return 0 end
redis.call('SADD', KEYS[7], ARGV[7])
redis.call('EXPIREAT', KEYS[7], tonumber(ARGV[8]))
if redis.call('SISMEMBER', KEYS[1], ARGV[2]) == 1 then
  redis.call('HSET', KEYS[6], ARGV[5], ARGV[6]); return 0
end
local old = redis.call('HGET', KEYS[2], 'data')
if old then redis.call('HSET', KEYS[6], ARGV[5], ARGV[6]); return 0 end
redis.call('HSET', KEYS[2], 'data', ARGV[3])
redis.call('SADD', KEYS[4], ARGV[1])
redis.call('ZADD', KEYS[5], ARGV[4], ARGV[1] .. '|ouvrir')
redis.call('HSET', KEYS[3], ARGV[5], cjson.encode({client_id=tostring(cjson.decode(ARGV[3]).client_id), message='Ton quiz sera accessible à la fin de la séance avec le QR code du billet', billet_id=ARGV[1]}))
redis.call('HSET', KEYS[6], ARGV[5], ARGV[6])
return 1
"""

START_LUA = r"""
local raw = redis.call('HGET', KEYS[1], 'data')
if not raw then return redis.error_reply('NOT_FOUND') end
local t = cjson.decode(raw)
if t.client_id ~= ARGV[2] then return redis.error_reply('FORBIDDEN') end
if redis.call('SISMEMBER', KEYS[2], t.seance_id) == 1 or t.statut == 'annulee' then return redis.error_reply('CANCELLED') end
local now = tonumber(ARGV[3])
if t.statut == 'en_cours' then return cjson.encode(t) end
if t.statut ~= 'ouverte' then return redis.error_reply('NOT_OPEN') end
if now > tonumber(t.demarrage_possible_jusqu_au_ts) then
  t.statut = 'expiree'; t.expiree_le = ARGV[4]
  redis.call('HSET', KEYS[1], 'data', cjson.encode(t)); redis.call('ZREM', KEYS[3], ARGV[1] .. '|demarrer')
  return redis.error_reply('EXPIRED')
end
local bankraw = redis.call('HVALS', KEYS[4])
if #bankraw < 10 then return redis.error_reply('NO_QUESTIONS') end
local bank = {}
for _, rawq in ipairs(bankraw) do table.insert(bank, cjson.decode(rawq)) end
local indices = {}
for i=1,#bank do indices[i]=i end
for i=#indices,2,-1 do local j=math.random(i); indices[i],indices[j]=indices[j],indices[i] end
local frozen = {}
for i=1,10 do
  local q = bank[indices[i]]
  local choices = {}
  for j=1,#q.choix do choices[j]=q.choix[j].choix_id end
  for j=#choices,2,-1 do local k=math.random(j); choices[j],choices[k]=choices[k],choices[j] end
  frozen[i] = {question_id=q.question_id, texte=q.texte, choix=q.choix, ordre_choix=choices, bonne_reponse_id=q.bonne_reponse_id}
end
t.questions_figees = frozen; t.reponses = {}; t.index_question = 1; t.statut = 'en_cours'; t.demarre_le = ARGV[4]
t.question_envoyee_le = ARGV[4]; t.question_echeance_ts = now + 15; t.termine_au_plus_tard_ts = now + 150
redis.call('HSET', KEYS[1], 'data', cjson.encode(t)); redis.call('ZADD', KEYS[3], now+15, ARGV[1] .. '|question')
return cjson.encode(t)
"""

ANSWER_LUA = r"""
local raw = redis.call('HGET', KEYS[1], 'data'); if not raw then return redis.error_reply('NOT_FOUND') end
local t = cjson.decode(raw)
if t.client_id ~= ARGV[2] then return redis.error_reply('FORBIDDEN') end
if t.statut ~= 'en_cours' then return redis.error_reply('NOT_RUNNING') end
local now = tonumber(ARGV[5]); local idx = tonumber(t.index_question)
if now >= tonumber(t.question_echeance_ts) then return redis.error_reply('QUESTION_EXPIRED') end
local q = t.questions_figees[idx]
if not q or q.question_id ~= ARGV[3] then return redis.error_reply('NOT_CURRENT') end
local choice_valid = false
for _,c in ipairs(q.ordre_choix) do if c == ARGV[4] then choice_valid = true end end
if not choice_valid then return redis.error_reply('INVALID_CHOICE') end
if t.reponses[q.question_id] then return redis.error_reply('ALREADY_ANSWERED') end
local correct = (ARGV[4] == q.bonne_reponse_id)
t.reponses[q.question_id] = {choix_id=ARGV[4], correcte=correct, repondue_le=ARGV[6]}
if correct then t.score = (t.score or 0) + 1 end
idx = idx + 1
if idx > 10 then
 t.statut='a_finaliser'; t.termine_le=ARGV[6]
 redis.call('ZREM', KEYS[2], ARGV[1] .. '|question')
 redis.call('ZADD', KEYS[2], now, ARGV[1] .. '|finaliser')
else
 t.index_question=idx; t.question_envoyee_le=ARGV[6]; t.question_echeance_ts=now+15
 redis.call('ZADD', KEYS[2], now+15, ARGV[1] .. '|question')
end
redis.call('HSET', KEYS[1], 'data', cjson.encode(t))
return cjson.encode(t)
"""

REWARD_LUA = r"""
local raw=redis.call('HGET', KEYS[1], 'data')
if not raw then return redis.error_reply('NOT_FOUND') end
local t=cjson.decode(raw)
if t.recompense_traitee then
  t.statut='terminee'
  redis.call('HSET', KEYS[1], 'data', cjson.encode(t))
  redis.call('ZREM', KEYS[9], ARGV[8] .. '|finaliser')
  return cjson.encode(t)
end
if t.statut ~= 'a_finaliser' and t.statut ~= 'terminee' then
  return redis.error_reply('NOT_FINISHED')
end
local score=tonumber(t.score or 0)
t.statut='terminee'
t.recompense_traitee=true; t.recompense_decidee_le=ARGV[1]; t.coupon_id=cjson.null
local quiz_event=cjson.decode(ARGV[5])
quiz_event.sequence_objet=redis.call('INCR', KEYS[7])
redis.call('RPUSH', KEYS[6], cjson.encode(quiz_event))
if score >= 8 then
  local coupon=cjson.decode(ARGV[3]); local coupon_event=cjson.decode(ARGV[6])
  coupon_event.sequence_objet=redis.call('INCR', KEYS[8])
  t.coupon_id=ARGV[2]
  redis.call('HSET', KEYS[2], ARGV[2], cjson.encode(coupon))
  redis.call('SADD', KEYS[3], ARGV[2])
  redis.call('RPUSH', KEYS[4], ARGV[4])
  redis.call('SET', KEYS[5], ARGV[7])
  redis.call('RPUSH', KEYS[6], cjson.encode(coupon_event))
end
redis.call('HSET', KEYS[1], 'data', cjson.encode(t))
return cjson.encode(t)
"""

PROCESS_DEADLINE_LUA = r"""
local raw=redis.call('HGET', KEYS[1], 'data')
if not raw then redis.call('ZREM', KEYS[3], ARGV[2] .. '|' .. ARGV[1]); return '{}' end
local t=cjson.decode(raw)
local action=ARGV[1]; local billet=ARGV[2]; local now=tonumber(ARGV[3])
-- Retirer l'échéance traitée avant de programmer la suivante.
redis.call('ZREM', KEYS[3], billet .. '|' .. action)
if action == 'ouvrir' and t.statut == 'programmee' then
  if redis.call('SISMEMBER', KEYS[2], t.seance_id) == 1 then
    t.statut='annulee'; t.annulee_le=ARGV[4]
  elseif now >= tonumber(t.demarrage_possible_jusqu_au_ts) then
    t.statut='expiree'; t.expiree_le=ARGV[4]
  else
    t.statut='ouverte'; t.ouverte_le=t.fin_seance
    local event=cjson.decode(ARGV[5]); event.sequence_objet=redis.call('INCR', KEYS[5])
    redis.call('RPUSH', KEYS[4], cjson.encode(event))
    redis.call('ZADD', KEYS[3], t.demarrage_possible_jusqu_au_ts, billet .. '|demarrer')
  end
elseif action == 'demarrer' and t.statut == 'ouverte' then
  if now >= tonumber(t.demarrage_possible_jusqu_au_ts) then t.statut='expiree'; t.expiree_le=ARGV[4] end
elseif action == 'question' and t.statut == 'en_cours' then
  while t.statut == 'en_cours' and now >= tonumber(t.question_echeance_ts) do
    local idx=tonumber(t.index_question); local q=t.questions_figees[idx]
    if q and not t.reponses[q.question_id] then t.reponses[q.question_id]={choix_id=cjson.null, correcte=false, repondue_le=cjson.null} end
    idx=idx+1
    if idx > 10 then
      t.statut='a_finaliser'; t.termine_le=ARGV[4]
      redis.call('ZADD', KEYS[3], now, billet .. '|finaliser')
    else
      t.index_question=idx; t.question_envoyee_le=t.question_echeance_ts
      t.question_echeance_ts=tonumber(t.question_echeance_ts)+15
    end
  end
  if t.statut == 'en_cours' then redis.call('ZADD', KEYS[3], t.question_echeance_ts, billet .. '|question') end
end
redis.call('HSET', KEYS[1], 'data', cjson.encode(t))
return cjson.encode(t)
"""

CANCEL_SESSION_LUA = r"""
local old=redis.call('HGET', KEYS[1], ARGV[1])
if old then if old ~= ARGV[2] then return redis.error_reply('ID_EVENT_CONFLICT') end return 0 end
redis.call('SADD', KEYS[2], ARGV[3])
local ids=redis.call('SMEMBERS', KEYS[3])
for _, billet in ipairs(ids) do
  local attempt_key=ARGV[4] .. billet
  local raw=redis.call('HGET', attempt_key, 'data')
  if raw then
    local t=cjson.decode(raw)
    if t.statut == 'programmee' or t.statut == 'ouverte' or t.statut == 'en_cours' then
      t.statut='annulee'; t.annulee_le=ARGV[5]
      if t.notification_id then redis.call('HDEL', ARGV[6] .. t.client_id, t.notification_id) end
      redis.call('HSET', attempt_key, 'data', cjson.encode(t))
      redis.call('ZREM', KEYS[4], billet .. '|ouvrir', billet .. '|demarrer', billet .. '|question')
    end
  end
end
redis.call('HSET', KEYS[1], ARGV[1], ARGV[2])
return #ids
"""

USE_COUPON_LUA = r"""
local raw=redis.call('HGET', KEYS[1], ARGV[1]); if not raw then return redis.error_reply('NOT_FOUND') end
local c=cjson.decode(raw)
if c.jeton_hash ~= ARGV[2] then return redis.error_reply('INVALID_TOKEN') end
if c.utilise then return redis.error_reply('ALREADY_USED') end
local now=tonumber(ARGV[3]); if now > tonumber(c.expire_le_ts) then return redis.error_reply('EXPIRED') end
if c.emis_date_locale == ARGV[4] then return redis.error_reply('SAME_DAY') end
if redis.call('SISMEMBER', KEYS[2], ARGV[4] .. '|' .. c.client_id) == 0 then return redis.error_reply('NO_VISIT_TODAY') end
local supplier=redis.call('HGET', KEYS[3], ARGV[5]); if not supplier then return redis.error_reply('PRODUCT_NOT_CONFIGURED') end
local seq=redis.call('INCR', KEYS[4])
c.utilise=true; c.produit_choisi=ARGV[5]; c.fournisseur_id=supplier; c.utilise_le=ARGV[6]; c.sequence_objet=seq
redis.call('HSET', KEYS[1], ARGV[1], cjson.encode(c))
local event=cjson.decode(ARGV[7]); event.sequence_objet=seq
redis.call('RPUSH', KEYS[5], cjson.encode(event))
redis.call('RPUSH', KEYS[6], cjson.encode({coupon_id=ARGV[1], produit_choisi=ARGV[5], fournisseur_id=supplier, utilise_le=ARGV[6]}))
return cjson.encode(c)
"""

class ReponseQuiz(BaseModel):
    question_id: str
    choix_id: str

class UtilisationCoupon(BaseModel):
    jeton_qr: str = Field(min_length=20, max_length=256)
    produit_choisi: str

class EvenementEntrant(BaseModel):
    evenement: dict[str, Any]


def maintenant() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime | None = None) -> str:
    return (dt or maintenant()).astimezone(UTC).isoformat().replace('+00:00', 'Z')


def json_compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True)


def redis_client(request: Request) -> Redis:
    return request.app.state.redis


def client_id_header(x_client_id: str | None = Header(default=None, alias='X-Client-Id')) -> str:
    if not x_client_id:
        raise HTTPException(401, 'En-tête X-Client-Id obligatoire')
    return x_client_id


def agent_id_header(x_agent_id: str | None = Header(default=None, alias='X-Agent-Id')) -> str:
    if not x_agent_id:
        raise HTTPException(401, 'En-tête X-Agent-Id obligatoire')
    return x_agent_id


def key(request: Request, suffix: str) -> str:
    return f"{request.app.state.redis_prefix}:{suffix}"


def erreur_redis(exc: Exception) -> HTTPException:
    msg = str(exc)
    mapping = {
        'NOT_FOUND': (404, 'Ressource introuvable'), 'FORBIDDEN': (403, 'Cette ressource appartient à un autre titulaire'),
        'CANCELLED': (409, 'La séance a été annulée'), 'NOT_OPEN': (409, 'Le quiz n’est pas ouvert'),
        'EXPIRED': (410, 'Le délai pour démarrer est dépassé'), 'NO_QUESTIONS': (409, 'Le film ne possède pas 10 questions disponibles'),
        'NOT_RUNNING': (409, 'La tentative n’est pas en cours'), 'QUESTION_EXPIRED': (409, 'Le délai de réponse est écoulé'),
        'NOT_CURRENT': (409, 'Cette question n’est pas la question courante'), 'INVALID_CHOICE': (422, 'Choix inconnu pour cette question'),
        'ALREADY_ANSWERED': (409, 'Une réponse a déjà été enregistrée'), 'INVALID_TOKEN': (403, 'Jeton QR invalide'),
        'ALREADY_USED': (409, 'Ce coupon a déjà été utilisé'), 'SAME_DAY': (409, 'Le coupon est utilisable lors d’une visite ultérieure'),
        'NO_VISIT_TODAY': (409, 'Aucun billet validé aujourd’hui pour ce titulaire'), 'PRODUCT_NOT_CONFIGURED': (503, 'Produit non configuré par le cinéma')}
    for marker, (code, detail) in mapping.items():
        if marker in msg: return HTTPException(code, detail)
    return HTTPException(503, 'Erreur lors du traitement Redis')


def _questions_public(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for q in questions:
        result.append({'question_id': q['question_id'], 'texte': q['texte'], 'choix': q['choix']})
    return result


def question_courante(t: dict[str, Any]) -> dict[str, Any] | None:
    if t.get('statut') != 'en_cours': return None
    q = t['questions_figees'][int(t['index_question']) - 1]
    return {'question_id': q['question_id'], 'texte': q['texte'],
            'choix': [next(c for c in q['choix'] if c['choix_id'] == cid) for cid in q['ordre_choix']],
            'numero': t['index_question'], 'echeance': iso(datetime.fromtimestamp(t['question_echeance_ts'], UTC))}


def _finaliser_tentative(redis: Redis, billet_id: str, t: dict[str, Any]) -> dict[str, Any]:
    """Finalise atomiquement le statut, l'événement et l'éventuel coupon."""
    now = maintenant(); decision = iso(now)
    quiz_event = {'id_evenement': 'evt_' + uuid.uuid4().hex, 'version_schema': 1, 'sequence_objet': 0,
        'type': 'quiz.termine', 'date': decision, 'emetteur': 'quiz', 'recepteurs': ['quiz'],
        'type_objet': 'Tentative', 'id_objet': billet_id,
        'modifications': [{'donnee':'statut','operation':'remplacer','nouvelle_valeur':'terminee'},
            {'donnee':'score','operation':'remplacer','nouvelle_valeur':int(t.get('score',0))},
            {'donnee':'termine_le','operation':'remplacer','nouvelle_valeur':t['termine_le']}],
        'contexte': {'client_id':t['client_id'],'film_id':t['film_id'],'seance_id':t['seance_id']}}
    coupon_id = 'cpn_' + uuid.uuid4().hex[:16] if int(t.get('score',0)) >= 8 else ''
    token = secrets.token_urlsafe(32) if coupon_id else ''
    expiry = now + timedelta(seconds=DUREE_COUPON)
    coupon = {'coupon_id':coupon_id,'client_id':t['client_id'],'tentative_id':billet_id,
        'film_origine_id':t['film_id'],'produits_eligibles':list(PRODUITS_ELIGIBLES),
        'produit_choisi':None,'fournisseur_id':None,'jeton_hash':hashlib.sha256(token.encode()).hexdigest() if token else '',
        'emis_le':decision,'expire_le':iso(expiry),'expire_le_ts':expiry.timestamp(),
        'emis_date_locale':now.astimezone(PARIS).date().isoformat(),'utilise':False}
    coupon_event = {'id_evenement':'evt_'+uuid.uuid4().hex,'version_schema':1,'sequence_objet':0,
        'type':'coupon.emis','date':decision,'emetteur':'quiz','recepteurs':['application_spectateur'],
        'type_objet':'Coupon','id_objet':coupon_id,
        'donnees':{k:coupon[k] for k in ('client_id','tentative_id','produits_eligibles','produit_choisi','film_origine_id','fournisseur_id','emis_le','expire_le','utilise')}}
    history = json_compact({'coupon_id':coupon_id,'tentative_id':billet_id,'emis_le':decision}) if coupon_id else ''
    result = redis.eval(REWARD_LUA, 9,
        f'{PREFIX}:tentative:{billet_id}', f'{PREFIX}:coupon:{coupon_id}', f'{PREFIX}:coupons:{t["client_id"]}',
        f'{PREFIX}:recompenses:{t["client_id"]}', f'{PREFIX}:coupon_token:{coupon_id}',
        f'{PREFIX}:evenements_a_publier', f'{PREFIX}:sequence:Tentative:{billet_id}', f'{PREFIX}:sequence:Coupon:{coupon_id}',
        f'{PREFIX}:echeances',
        decision, coupon_id, json_compact(coupon), history, json_compact(quiz_event), json_compact(coupon_event), token, billet_id)
    return json.loads(result)


@asynccontextmanager
async def lifespan(application: FastAPI):
    redis = Redis.from_url(os.environ['REDIS_URL'], decode_responses=True,
                           socket_connect_timeout=2, socket_timeout=5)
    application.state.redis = redis
    application.state.redis_prefix = os.environ.get('REDIS_PREFIX', 'quiz')
    redis.ping()

    # Imports locaux : reception.py importe traiter_evenement depuis ce module.
    # L'import local évite une importation circulaire au chargement de main.py.
    from services.commun.livraison import Livraison
    from services.quiz.reception import ReceptionRedis

    arret_publication = threading.Event()
    arret_livraison = threading.Event()
    livraison = Livraison(
        redis,
        ReceptionRedis(redis, application.state.redis_prefix),
        application.state.redis_prefix,
    )
    thread_livraison = threading.Thread(
        target=livraison.executer,
        args=(arret_livraison,),
        name='quiz-livraison',
        daemon=True,
    )
    thread_livraison.start()

    publication = Publication(redis, BoiteEnvoiRedis(redis, application.state.redis_prefix), application.state.redis_prefix)
    thread_publication = threading.Thread(
        target=publication.executer,
        args=(arret_publication,),
        name='quiz-publication',
        daemon=True,
    )
    thread_publication.start()

    task = asyncio.create_task(worker_echeances(application))
    application.state.deadline_task = task
    try:
        yield
    finally:
        arret_livraison.set()
        arret_publication.set()
        thread_livraison.join(timeout=5)
        thread_publication.join(timeout=5)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        redis.close()

app = FastAPI(title='CinéQuiz - quiz', lifespan=lifespan)

@app.get('/health')
def health(request: Request):
    try: request.app.state.redis.ping()
    except RedisError as exc: raise HTTPException(503, 'Redis indisponible') from exc
    return {'service': 'quiz', 'statut': 'ok'}

@app.get('/')
def accueil():
    return {'service': 'quiz', 'documentation': '/docs'}

@app.get('/films/{film_id}/questions')
def lire_questions(film_id: str, request: Request):
    bank = redis_client(request).hvals(key(request, f'questions:{film_id}'))
    if not bank: raise HTTPException(404, 'Film inconnu dans le réplica Quiz')
    return {'film_id': film_id, 'questions': _questions_public([json.loads(q) for q in bank])}

@app.get('/notifications')
def mes_notifications(request: Request, client: str = Depends(client_id_header)):
    messages = redis_client(request).hvals(key(request, f'notifications:{client}'))
    return {'notifications': [json.loads(message) for message in messages]}

@app.post('/internal/evenements', status_code=202)
def recevoir_evenement(body: EvenementEntrant, request: Request):
    """Point d'entrée de test Swagger; en production le worker de livraison appelle le même handler."""
    try:
        traiter_evenement(redis_client(request), body.evenement)
    except (ValueError, KeyError, TypeError, ValidationError) as exc:
        raise HTTPException(422, f'Événement invalide : {exc}') from exc
    return {'statut': 'reçu', 'id_evenement': body.evenement.get('id_evenement')}

@app.get('/internal/evenements/en-attente')
def consulter_outbox(request: Request, limite: int = 50):
    if not 1 <= limite <= 200: raise HTTPException(422, 'limite doit être comprise entre 1 et 200')
    messages = redis_client(request).lrange(key(request, 'evenements_a_publier'), 0, limite - 1)
    return {'nombre': len(messages), 'evenements': [json.loads(m) for m in messages]}


def traiter_evenement(redis: Redis, event: dict[str, Any]) -> bool:
    required = {'id_evenement','version_schema','sequence_objet','type','date','emetteur','recepteurs','type_objet','id_objet'}
    if not required.issubset(event): raise ValueError('Enveloppe événement incomplète')
    Evenement.model_validate(event)
    if event['version_schema'] != 1: raise ValueError('Version de schéma inconnue')
    if 'quiz' not in event.get('recepteurs', []) and event.get('type') != 'quiz.termine':
        raise ValueError('Événement non adressé au service Quiz')
    eid = event['id_evenement']; fingerprint = hashlib.sha256(json_compact(event).encode()).hexdigest()
    done_key = f'{PREFIX}:evenements_traites'
    if event['type'] in ('film.cree','film.modifie'):
        film_id = event['id_objet']; title = (event.get('donnees') or {}).get('titre') or redis.hget(f'{PREFIX}:film:{film_id}', 'titre') or ''
        current = [json.loads(q) for q in redis.hvals(f'{PREFIX}:questions:{film_id}')]
        if event['type'] == 'film.cree':
            data = event['donnees']; title = data.get('titre', '')
            questions = data.get('questions', [])
        else:
            questions = current
            for mod in event.get('modifications', []):
                if mod.get('donnee') == 'titre' and mod.get('operation') == 'remplacer': title = mod['nouvelle_valeur']
                if mod.get('donnee') == 'questions':
                    if mod.get('operation') == 'remplacer': questions = mod['nouvelle_valeur']
                    elif mod.get('operation') == 'ajouter': questions = [*questions, mod['nouvelle_valeur']]
        question_ids = [q.get('question_id') for q in questions]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError('Les question_id doivent être uniques dans un film')
        for q in questions:
            if not all(k in q for k in ('question_id','texte','choix','bonne_reponse_id')):
                raise ValueError('Question incomplète')
            choice_ids = [c.get('choix_id') for c in q['choix']]
            if len(choice_ids) != 4 or any(not isinstance(cid, str) or not cid for cid in choice_ids) or len(set(choice_ids)) != 4 or q['bonne_reponse_id'] not in choice_ids:
                raise ValueError(f"Chaque question doit avoir quatre choix uniques et une bonne réponse valide ({q['question_id']})")
            if any(not isinstance(c.get('texte'), str) or not c.get('texte') for c in q['choix']):
                raise ValueError(f"Texte de choix manquant ({q['question_id']})")
        redis.eval(SYNC_FILM_LUA, 3, done_key, f'{PREFIX}:film:{film_id}', f'{PREFIX}:questions:{film_id}', eid, fingerprint, title or '', json_compact(questions))
        return True
    if event['type'] == 'billet.valide':
        c = event.get('contexte') or {}; billet_id = c.get('billet_id'); client = c.get('titulaire_id')
        seance = c.get('seance_id'); film = c.get('film_id'); fin = c.get('fin_seance')
        if not all((billet_id, client, seance, film, fin)): raise ValueError('contexte billet.valide incomplet')
        end = datetime.fromisoformat(fin.replace('Z','+00:00')).astimezone(UTC)
        limit = end + timedelta(seconds=DELAI_DEMARRAGE)
        t = {'billet_id': billet_id, 'client_id': client, 'film_id': film, 'seance_id': seance,
             'statut': 'programmee', 'fin_seance': iso(end), 'fin_seance_ts': end.timestamp(),
             'demarrage_possible_jusqu_au': iso(limit), 'demarrage_possible_jusqu_au_ts': limit.timestamp(),
             'notification_id': eid, 'questions_figees': [], 'reponses': {}, 'score': 0, 'recompense_traitee': False, 'coupon_id': None}
        # Le contrat place valide_le dans le billet de la liste modifiée ;
        # on sélectionne uniquement le billet_id du contexte, jamais un autre billet.
        valid = c.get('valide_le')
        if not valid:
            for modification in event.get('modifications') or []:
                if modification.get('donnee') != 'billets' or modification.get('operation') != 'remplacer':
                    continue
                billets = modification.get('nouvelle_valeur') or []
                billet = next((b for b in billets if b.get('billet_id') == billet_id), None)
                if billet:
                    valid = billet.get('valide_le')
                    break
        # Certains événements de test plus anciens ne portent pas valide_le ;
        # dans ce cas seulement, la date de l'événement sert de repli.
        valid = valid or event['date']
        day = datetime.fromisoformat(valid.replace('Z','+00:00')).astimezone(PARIS).date().isoformat()
        expiry_visit = int(datetime.combine(datetime.fromisoformat(day).date()+timedelta(days=2), datetime.min.time(), PARIS).timestamp())
        redis.eval(CREATE_ATTEMPT_LUA, 7, f'{PREFIX}:seances_annulees', f'{PREFIX}:tentative:{billet_id}', f'{PREFIX}:notifications:{client}',
                   f'{PREFIX}:tentatives_par_seance:{seance}', f'{PREFIX}:echeances', done_key, f'{PREFIX}:visites:{day}',
                   billet_id, seance, json_compact(t), end.timestamp(), eid, fingerprint, f'{day}|{client}', expiry_visit)
        return True
    if event['type'] == 'quiz.termine':
        return bool(redis.eval(RECEPTION_LUA, 1, done_key, eid, fingerprint))
    if event['type'] == 'seance.annulee':
        seance = event['id_objet']
        redis.eval(CANCEL_SESSION_LUA, 4, done_key, f'{PREFIX}:seances_annulees',
                   f'{PREFIX}:tentatives_par_seance:{seance}', f'{PREFIX}:echeances',
                   eid, fingerprint, seance, f'{PREFIX}:tentative:', iso(), f'{PREFIX}:notifications:')
        return True
    raise ValueError(f"Type d'événement non pris en charge : {event['type']}")

@app.post('/quiz/{billet_id}/demarrer')
def demarrer_quiz(billet_id: str, request: Request, client: str = Depends(client_id_header)):
    now = maintenant()
    try:
        r = redis_client(request)
        attempt_raw = r.hget(key(request, f'tentative:{billet_id}'), 'data')
        if not attempt_raw: raise HTTPException(404, 'Tentative introuvable')
        attempt = json.loads(attempt_raw)
        if attempt.get('client_id') != client: raise HTTPException(403, 'Cette tentative appartient à un autre titulaire')
        raw = r.eval(START_LUA, 4, key(request, f'tentative:{billet_id}'), key(request, 'seances_annulees'),
            key(request, 'echeances'), key(request, f'questions:{attempt["film_id"]}'), billet_id, client, now.timestamp(), iso(now))
        t = json.loads(raw)
    except RedisError as exc: raise erreur_redis(exc) from exc
    return {'billet_id': billet_id, 'statut': t['statut'], 'question': question_courante(t)}

@app.post('/quiz/{billet_id}/reponses')
def repondre(billet_id: str, body: ReponseQuiz, request: Request, client: str = Depends(client_id_header)):
    now = maintenant(); r = redis_client(request)
    try:
        raw = r.eval(ANSWER_LUA, 2, key(request, f'tentative:{billet_id}'), key(request, 'echeances'), billet_id, client,
                    body.question_id, body.choix_id, now.timestamp(), iso(now))
        t = json.loads(raw)
        if t['statut'] == 'a_finaliser': t = _finaliser_tentative(r, billet_id, t)
    except RedisError as exc: raise erreur_redis(exc) from exc
    return {'billet_id': billet_id, 'statut': t['statut'], 'score_provisoire': t.get('score', 0),
            'question': question_courante(t), 'resultat': ({'score': t['score'], 'coupon_id': t.get('coupon_id')} if t['statut']=='terminee' else None)}

@app.get('/quiz/{billet_id}')
def consulter_tentative(billet_id: str, request: Request, client: str = Depends(client_id_header)):
    raw = redis_client(request).hget(key(request, f'tentative:{billet_id}'), 'data')
    if not raw: raise HTTPException(404, 'Tentative introuvable')
    t = json.loads(raw)
    if t['client_id'] != client: raise HTTPException(403, 'Cette tentative appartient à un autre titulaire')
    return {'billet_id': billet_id, 'statut': t['statut'], 'score': t.get('score') if t['statut'] in ('terminee','expiree') else None,
            'question': question_courante(t), 'termine_le': t.get('termine_le'), 'coupon_id': t.get('coupon_id')}

@app.get('/coupons')
def mes_coupons(request: Request, client: str = Depends(client_id_header)):
    r = redis_client(request); result=[]
    for cid in r.smembers(key(request, f'coupons:{client}')):
        raw = r.hget(key(request, f'coupon:{cid}'), cid)
        if raw:
            c=json.loads(raw)
            result.append({k:c.get(k) for k in ('coupon_id','produits_eligibles','emis_le','expire_le','utilise','produit_choisi')})
    return {'coupons': sorted(result, key=lambda c:c['emis_le'], reverse=True)}

@app.get('/coupons/{coupon_id}/qr')
def obtenir_qr(coupon_id: str, request: Request, client: str = Depends(client_id_header)):
    r=redis_client(request); raw=r.hget(key(request, f'coupon:{coupon_id}'), coupon_id)
    if not raw: raise HTTPException(404, 'Coupon introuvable')
    c=json.loads(raw)
    if c['client_id'] != client: raise HTTPException(403, 'Ce coupon appartient à un autre titulaire')
    if c['utilise']: raise HTTPException(409, 'Coupon déjà utilisé')
    if maintenant().timestamp() > c['expire_le_ts']: raise HTTPException(410, 'Coupon expiré')
    token=r.get(key(request, f'coupon_token:{coupon_id}'))
    if not token: raise HTTPException(410, 'QR code indisponible')
    return {'coupon_id': coupon_id, 'jeton_qr': token, 'expire_le': c['expire_le']}

@app.post('/coupons/{coupon_id}/utiliser')
def utiliser_coupon(coupon_id: str, body: UtilisationCoupon, request: Request, agent: str = Depends(agent_id_header)):
    # L'agent est authentifié par l'en-tête simulé; le produit et le fournisseur sont configurés côté serveur.
    r=redis_client(request); now=maintenant(); token_hash=hashlib.sha256(body.jeton_qr.encode()).hexdigest()
    try:
        config=json.loads(os.getenv('COUPON_PRODUCTS_JSON', json.dumps(FOURNISSEURS_PAR_PRODUIT)))
        if not isinstance(config, dict): raise ValueError('La configuration doit être un objet JSON')
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(500, 'COUPON_PRODUCTS_JSON est invalide') from exc
    for product, supplier in config.items():
        if isinstance(product, str) and isinstance(supplier, str) and supplier:
            r.hset(key(request,'produits_fournisseurs'), product, supplier)
    if body.produit_choisi not in PRODUITS_ELIGIBLES: raise HTTPException(422, 'Produit non éligible')
    # Outbox complète et preuve d'utilisation écrites par le script Lua.
    try:
        # Construire l'événement avant l'appel, avec séquence réservée par Lua.
        seqkey=key(request, f'sequence:Coupon:{coupon_id}')
        seq=0
        raw=r.hget(key(request,f'coupon:{coupon_id}'),coupon_id)
        if not raw: raise HTTPException(404,'Coupon introuvable')
        c=json.loads(raw)
        supplier=r.hget(key(request,'produits_fournisseurs'),body.produit_choisi)
        if not supplier: raise HTTPException(503,'Produit non configuré par le cinéma')
        event={'id_evenement':'evt_'+uuid.uuid4().hex,'version_schema':1,'sequence_objet':seq,'type':'coupon.utilise',
               'date':iso(now),'emetteur':'quiz','recepteurs':['fournisseurs'],'type_objet':'Coupon','id_objet':coupon_id,
               'modifications':[{'donnee':'produit_choisi','operation':'remplacer','nouvelle_valeur':body.produit_choisi},
                {'donnee':'fournisseur_id','operation':'remplacer','nouvelle_valeur':supplier},
                {'donnee':'utilise','operation':'remplacer','nouvelle_valeur':True},
                {'donnee':'utilise_le','operation':'remplacer','nouvelle_valeur':iso(now)}],
               'contexte':{'produit_choisi':body.produit_choisi,'fournisseur_id':supplier,'film_origine_id':c['film_origine_id']}}
        r.eval(USE_COUPON_LUA,6,key(request,f'coupon:{coupon_id}'),key(request,f'visites:{now.astimezone(PARIS).date().isoformat()}'),key(request,'produits_fournisseurs'),seqkey,
               key(request,'evenements_a_publier'),key(request,'utilisations'),coupon_id,token_hash,now.timestamp(),now.astimezone(PARIS).date().isoformat(),
               body.produit_choisi,iso(now),json_compact(event))
        r.delete(key(request,f'coupon_token:{coupon_id}'))
        return {'statut':'utilise','coupon_id':coupon_id,'produit_choisi':body.produit_choisi,'utilise_le':iso(now),'agent_id':agent}
    except RedisError as exc: raise erreur_redis(exc) from exc

async def worker_echeances(app: FastAPI):
    """Traite durablement les ouvertures, expirations et délais de réponse."""
    while True:
        try:
            r = app.state.redis
            now = maintenant()
            due = r.zrangebyscore(f'{PREFIX}:echeances', '-inf', now.timestamp(), start=0, num=100)
            for member in due:
                billet, action = member.rsplit('|', 1)
                attempt_raw = r.hget(f'{PREFIX}:tentative:{billet}', 'data')
                if not attempt_raw:
                    r.zrem(f'{PREFIX}:echeances', member)
                    continue
                attempt = json.loads(attempt_raw)
                if action == 'finaliser':
                    _finaliser_tentative(r, billet, attempt)
                    continue
                event = {}
                if action == 'ouvrir':
                    event = {'id_evenement':'evt_'+uuid.uuid4().hex,'version_schema':1,'sequence_objet':0,
                        'type':'quiz.ouvert','date':iso(now),'emetteur':'quiz','recepteurs':['application_spectateur'],
                        'type_objet':'Tentative','id_objet':billet,
                        'modifications':[{'donnee':'statut','operation':'remplacer','nouvelle_valeur':'ouverte'},
                            {'donnee':'demarrage_possible_jusqu_au','operation':'remplacer','nouvelle_valeur':attempt['demarrage_possible_jusqu_au']}],
                        'contexte':{'client_id':attempt['client_id'],'film_id':attempt['film_id'],
                                    'seance_id':attempt['seance_id'],'fin_seance':attempt['fin_seance']}}
                raw = r.eval(PROCESS_DEADLINE_LUA, 5, f'{PREFIX}:tentative:{billet}',
                    f'{PREFIX}:seances_annulees', f'{PREFIX}:echeances', f'{PREFIX}:evenements_a_publier',
                    f'{PREFIX}:sequence:Tentative:{billet}', action, billet, now.timestamp(), iso(now), json_compact(event))
                updated = json.loads(raw)
                # La dernière réponse expirée programme une échéance durable
                # |finaliser ; le worker la traitera au prochain passage.
        except (RedisError, ValueError, KeyError, TypeError):
            # L'échéance reste durable et sera retentée au prochain passage.
            pass
        await asyncio.sleep(0.2)
