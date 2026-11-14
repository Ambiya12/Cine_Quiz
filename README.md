# CinéQuiz

Projet NoSQL : l'application d'un cinéma fictif, découpée en services. Le spectateur
réserve sa séance, puis répond à un quiz sur le film qu'il vient de voir pour tenter
de gagner un coupon pour un popcorn ou une boisson lors d'une prochaine visite.

Chaque service possède ses propres données et sa propre base NoSQL. Les services
communiquent uniquement par événements, et gardent des réplicas des données dont
ils ont besoin pour rester autonomes.

## Le parcours en bref

1. **Préparation** : le gérant crée les films, leurs questions et les séances.
2. **Achat** : le spectateur réserve, choisit ses tarifs et paie ; un billet par place.
3. **Entrée** : un agent scanne chaque billet et vérifie les justificatifs de tarif réduit.
4. **Scan du QR Code** : le titulaire scanne le QR code figurant sur son billet à la fin de la séance ; il dispose de 2 heures pour démarrer le quiz.
5. **Quiz** : 10 questions, 15 secondes chacune, au plus 150 secondes après le démarrage ; les choix mélangés gardent leurs identifiants et leur ordre après reconnexion.
6. **Récompense** : Aucun coupon de 0 à 7/10 ; coupon garanti à 08/10 ; un popcorn moyen ou une boisson au choix à une visite ultérieure.

7. **Prochaine visite** : le client choisit un popcorn ou une boisson au comptoir ; le coupon est valable 30 jours et utilisable une seule fois ; le fournisseur reçoit le total du produit réellement remis.

## Les services

| Service | Base | Agrégats |
|---|---|---|
| Catalogue | MongoDB | Film, Salle, Séance |
| Réservations | MongoDB | Réservation |
| Quiz et coupons | Redis | Tentative, Coupon |
| Recherche | Elasticsearch | Aucun (projection) |
| Fournisseurs | MongoDB | Aucun (projection) |

Le périmètre comprend trois services métier et deux services de projection.
Le service Quiz et coupons utilise l'identifiant `quiz` dans les contrats et les
chemins du dépôt. Les identifiants des clients proviennent du prestataire
d'authentification simulé ; la gestion des profils reste hors périmètre.

Le socle exécutable lance Catalogue, Réservations, Quiz et Fournisseurs.
Les commandes métier, les workers et les consommateurs Recherche et
`application_spectateur` seront ajoutés dans les lots suivants.

## Démarrage local

Avec Docker et Docker Compose v2, lancer cette commande depuis la racine du dépôt.

```sh
docker compose up --build
```

| Service | Vérification dans Postman | Documentation OpenAPI |
|---|---|---|
| Catalogue | `GET http://localhost:8081/health` | [Swagger UI](http://localhost:8081/docs) |
| Réservations | `GET http://localhost:8082/health` | [Swagger UI](http://localhost:8082/docs) |
| Quiz | `GET http://localhost:8083/health` | [Swagger UI](http://localhost:8083/docs) |
| Fournisseurs | `GET http://localhost:8084/health` | [Swagger UI](http://localhost:8084/docs) |

Chaque route `/health` renvoie HTTP 200 avec le nom du service et `"statut": "ok"`
si ses dépendances sont joignables, ou HTTP 503 sinon. Compose attend que Redis
réponde et que MongoDB soit primaire dans le replica set `rs0` pour les services
qui utilisent MongoDB.

Les services partagent l'instance Redis via `REDIS_URL`. `REDIS_PREFIX` vaut le
nom du service pour ses clés métier, par exemple `quiz:evenements_a_publier`.
Les clés de transport conservent les noms du contrat, par exemple
`evenements:quiz:ready`. Catalogue, Réservations et Fournisseurs utilisent
`MONGO_URI` et une base par service, définie par `MONGO_DB`
(`cinequiz_catalogue`, `cinequiz_reservations`, `cinequiz_fournisseurs`).

Voir [l'interface commune des événements](services/commun/README.md) pour
implémenter un producteur ou un consommateur.

## Équipe

- AMBIYA DIMAS Galystan
- DJANANG KOUOMOU Amery
- BARUA Nirzara
- NJONZO TAKAM Stephanie
- HIRA Amena

## Organisation du dépôt

Les dossiers suivent les étapes des consignes du projet.

| Élément | Contenu |
|---|---|
| `index.qmd` | Le rapport complet : descriptif, parcours et justifications |
| `DECISIONS.md` | Les choix de conception et leur validation par l'équipe |
| `2-services-commandes/` | Les services et leurs commandes |
| `3-agregats/` | Les six agrégats, leurs lectures, clés de stockage et règles de conservation |
| `4-evenements-replicas/` | Les événements entre services et les réplicas |
| `5-projections/` | Les projections vers de nouveaux services et leurs requêtes (étape 6 des consignes) |
| `services/` | Les API FastAPI et leur module commun |



## Conventions

- Les identifiants des exemples sont lisibles (`client_12`, `film_dune2`) pour faciliter
  la compréhension. Dans l'application, ils sont générés automatiquement par le service
  propriétaire de l'agrégat.
- Les noms de fichiers, de dossiers et de clés JSON sont sans accents ni espaces.

## Lire le rapport

Le rapport est écrit avec [Quarto](https://quarto.org). Pour le générer :

    quarto render

Pour le voir se mettre à jour pendant l'écriture :

    quarto preview
