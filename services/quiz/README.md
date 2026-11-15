# Service Quiz et coupons

Ce service implémente la partie **Stephanie — Quiz et coupons**. Il utilise Redis et ne modifie pas les autres services du dépôt.

## Démarrage

Depuis la racine du dépôt :

```bash
docker compose up -d --build quiz
```

Swagger UI : <http://localhost:8083/docs>
Contrôle de santé : <http://localhost:8083/health>

Le service démarre son worker d'échéances et le worker commun de publication dans son cycle de vie FastAPI. Redis doit être persistant et configuré avec AOF et `noeviction` comme dans `docker-compose.yml`.

Le worker commun de livraison n'est pas encore démarré par Quiz : `services/commun/livraison.py` n'est pas présent dans cette version du dépôt. L'adaptateur `ReceptionRedis` est préparé pour son protocole, mais la consommation automatique de `evenements:quiz:ready` attend ce worker.

## Configuration des produits

Le fournisseur est choisi côté serveur. Le service reprend par défaut les deux associations présentes dans les exemples du dépôt. Elles peuvent être surchargées avec `COUPON_PRODUCTS_JSON` dans l'environnement du conteneur Quiz. C'est un objet JSON `produit -> fournisseur_id`, par exemple :

```json
{"popcorn_moyen":"fourn_popcorn_1","boisson":"fourn_boisson_1"}
```

Les identifiants par défaut sont ceux des exemples de contrats du projet ; l'équipe peut les remplacer par la configuration réelle du cinéma. Les deux produits éligibles sont `popcorn_moyen` et `boisson`.

## En-têtes Swagger

- `X-Client-Id` : spectateur titulaire de la tentative ou du coupon.
- `X-Agent-Id` : agent qui scanne un coupon au comptoir.

Ces en-têtes simulent l'identité pendant le projet. Ils ne remplacent pas une authentification réelle.

## Endpoints

| Méthode | Route | Rôle |
|---|---|---|
| `GET` | `/health` | Vérifier Redis |
| `POST` | `/internal/evenements` | Injecter un événement entrant pour les tests Swagger |
| `GET` | `/internal/evenements/en-attente` | Voir les événements Quiz en attente de publication |
| `GET` | `/films/{film_id}/questions` | Voir les questions répliquées, sans les bonnes réponses |
| `GET` | `/notifications` | Consulter les notifications préparées pour soi (`X-Client-Id`) |
| `POST` | `/quiz/{billet_id}/demarrer` | Démarrer ou reprendre le quiz (`X-Client-Id`) |
| `POST` | `/quiz/{billet_id}/reponses` | Répondre à la question courante (`X-Client-Id`) |
| `GET` | `/quiz/{billet_id}` | Consulter sa tentative (`X-Client-Id`) |
| `GET` | `/coupons` | Lister ses coupons (`X-Client-Id`) |
| `GET` | `/coupons/{coupon_id}/qr` | Obtenir le jeton QR privé (`X-Client-Id`) |
| `POST` | `/coupons/{coupon_id}/utiliser` | Scanner et utiliser le coupon (`X-Agent-Id`) |

## Événements entrants

Le service consomme `film.cree`, `film.modifie`, `billet.valide` et `seance.annulee`. La route `/internal/evenements` est un adaptateur de test Swagger. En intégration, le worker de livraison commun devra appeler l'adaptateur `ReceptionRedis.appliquer(evenement, message, sequence_precedente)` et n'acquitter le message qu'après le retour sans erreur. Conformément au contrat commun, ce worker doit vérifier dans le journal que les éventuels trous de séquence correspondent à des événements destinés à d'autres services. L'adaptateur Quiz accepte une séquence strictement supérieure à la progression locale ; il ne valide pas lui-même les événements intermédiaires.

Le traitement de `film.*`, la création d'une tentative et l'enregistrement de l'événement reçu sont atomiques dans Redis. Une séance annulée est marquée avant d'annuler les tentatives non terminées ; les tentatives terminées et leurs coupons sont conservés. Pour le raccordement au worker commun, `services.quiz.reception.ReceptionRedis` implémente le protocole `Reception`, et `services.quiz.boite_envoi.BoiteEnvoiRedis` implémente `BoiteEnvoi`.

## Événements sortants

Les événements sont ajoutés à la liste Redis `quiz:evenements_a_publier`, consommée par le worker de publication commun :

- `quiz.ouvert` à la fin de la séance ;
- `quiz.termine` après les dix questions ou leurs expirations ;
- `coupon.emis` si le score est au moins 8/10 ;
- `coupon.utilise` après un scan valide.

Le contenu de l'outbox peut être inspecté dans Swagger avec `GET /internal/evenements/en-attente`. `BoiteEnvoiRedis.prochain_message()` retourne le premier message sans le retirer ; `confirmer_publication(id_evenement)` ne le retire qu'après distribution réussie dans le journal et les files des destinataires. Le worker `Publication` commun est lancé automatiquement par le cycle de vie du service Quiz.

## Règles métier implémentées

Une tentative est créée une seule fois par billet validé, programmée à la fin de la séance et démarrable pendant les deux heures suivantes. Au démarrage, dix questions et l'ordre des choix sont figés. Chaque question dure quinze secondes ; les délais écoulés pendant une déconnexion sont comptés faux. La correction et le score restent côté serveur.

Une récompense est décidée une seule fois : aucun coupon sous 8/10, un coupon garanti à partir de 8/10. Le coupon est valable trente jours, est lié au titulaire et à sa tentative, et ne peut être utilisé qu'une seule fois, un autre jour que celui de son émission, avec un billet validé le jour de l'utilisation (jour local `Europe/Paris`). Le jeton QR n'est jamais inclus dans les événements.

Les transitions sensibles (réception, démarrage, réponse, échéances, attribution de récompense et utilisation) sont protégées par des scripts Lua Redis. Les clés de tentative et de coupon sont distinctes, avec les index par séance et par titulaire.

## Parcours de test Swagger conseillé

1. Injecter `film.cree` puis `film.modifie` pour un film contenant au moins dix questions.
2. Injecter `billet.valide` avec `billet_id`, `titulaire_id`, `seance_id`, `film_id` et `fin_seance` dans `contexte`.
3. Pour ne pas attendre l'heure réelle de fin, utiliser un événement de test dont `fin_seance` est passée mais dont la fenêtre de deux heures n'est pas dépassée ; le worker ouvrira alors le quiz.
4. Appeler `POST /quiz/{billet_id}/demarrer` avec `X-Client-Id`, puis répondre à chaque question avec son `question_id` et un `choix_id`.
5. À 8/10 ou plus, récupérer le coupon avec `GET /coupons`, puis son jeton avec `GET /coupons/{coupon_id}/qr`.
6. Utiliser le coupon avec `POST /coupons/{coupon_id}/utiliser`, `X-Agent-Id`, le jeton et le produit choisi. Répéter le scan : la seconde requête doit être refusée.
7. Consulter `GET /internal/evenements/en-attente` pour vérifier `quiz.termine`, `coupon.emis` et `coupon.utilise`.

Pour les tests, chaque événement injecté doit respecter le contrat commun (enveloppe `evenement`, version de schéma 1, séquence positive, date UTC et une seule charge utile `donnees` ou `modifications`).
