# CinéQuiz

CinéQuiz est un projet NoSQL qui simule le parcours d'un spectateur, de la réservation d'une place à l'utilisation d'un coupon au comptoir. Après la séance, le spectateur peut répondre à un quiz de 10 questions sur le film. À partir de 8/10, il reçoit un coupon pour un popcorn moyen ou une boisson, valable 30 jours et utilisable une seule fois lors d'une visite ultérieure.

Chaque service possède ses données et échange des événements via Redis. Le paiement et l'authentification sont simulés.

## Prérequis

- Git pour cloner le dépôt.
- Docker Desktop démarré, avec Docker Compose v2.
- Python 3.9 ou plus récent pour charger les données de démonstration. Le script utilise la bibliothèque standard.
- Postman pour exécuter les collections de tests.

## Démarrer le projet

Clonez le dépôt, puis lancez les services depuis sa racine.

```sh
git clone https://github.com/Ambiya12/Cine_Quiz.git
cd Cine_Quiz
docker compose up -d --build --wait
docker compose ps
```

Compose démarre MongoDB, Redis et les quatre API. Il attend que Redis soit joignable et que MongoDB soit primaire dans le replica set `rs0`. Les transactions MongoDB s'appuient sur ce replica set.

| Service | Base de données | Vérification de santé | Documentation interactive |
| --- | --- | --- | --- |
| Catalogue | MongoDB | [Port 8081](http://localhost:8081/health) | [Swagger UI](http://localhost:8081/docs) |
| Réservations | MongoDB | [Port 8082](http://localhost:8082/health) | [Swagger UI](http://localhost:8082/docs) |
| Quiz et coupons | Redis | [Port 8083](http://localhost:8083/health) | [Swagger UI](http://localhost:8083/docs) |
| Fournisseurs | MongoDB | [Port 8084](http://localhost:8084/health) | [Swagger UI](http://localhost:8084/docs) |

Les routes `/health` renvoient `200 OK` avec `"statut": "ok"` lorsque les dépendances répondent. Pour consulter les logs d'un service, utilisez par exemple `docker compose logs catalogue`.

### Charger les données de démonstration

Après le démarrage du Catalogue, exécutez la commande suivante depuis la racine du dépôt.

```sh
python3 scripts/donnees_demo.py
```

Le script crée trois films avec dix questions chacun et affiche leurs identifiants. Les événements produits par l'API alimentent la banque de questions du Quiz. Exécutez le script une fois par environnement de démonstration, car chaque exécution crée trois nouveaux films.

## Tester avec Postman

### Catalogue

1. Ouvrez **Import** et ajoutez la [collection CinéQuiz](postman/CineQuiz.postman_collection.json) et l'[environnement CinéQuizLocal](postman/CineQuizLocal.postman_environment.json).
2. Sélectionnez **CinéQuizLocal** et vérifiez que la variable `catalogue` vaut `http://localhost:8081`.
3. Ouvrez la collection **CinéQuiz**, choisissez **Run** et exécutez **Catalogue - Films** et **Règle 409 : séance à venir** dans l'ordre proposé, avec une itération. Les scénarios créent leurs propres films et salles. Le Runner renseigne les identifiants et exécute la boucle qui ajoute les dix questions.
4. Consultez **Test Results**. Une durée hors limites doit renvoyer `422`, un conflit de créneau doit renvoyer `409` et la reprogrammation d'une séance annulée doit renvoyer `201` avec un nouvel identifiant.

### Réservations

Importez la [collection Réservations](services/reservations/postman/reservations.postman_collection.json), vérifiez que sa variable `base_url` vaut `http://localhost:8082`, puis exécutez-la dans le Runner. Elle injecte une séance de test, bloque et réserve des places, valide les billets, vérifie la publication de `billet.valide` et contrôle le remboursement après annulation.

La séance de test commence deux heures après le lancement. Pour que la validation du billet fonctionne, exécutez le scénario avant 22 h, heure de Paris, afin que la séance soit programmée le jour même.

### Quiz et Fournisseurs

- Après le chargement des données de démonstration, appelez `GET /films/{film_id}/questions` sur le port `8083` avec l'un des identifiants affichés. La réponse doit contenir dix questions. La synchronisation passe par Redis et peut prendre quelques secondes.
- Pour parcourir le flux complet, créez une salle et une séance dans Catalogue, réservez et validez un billet dans Réservations, puis démarrez le quiz à la fin de la séance. Utilisez l'en-tête `X-Client-Id` avec l'identifiant du titulaire pour démarrer le quiz, répondre aux questions et consulter son coupon.
- Pour tester l'utilisation d'un coupon, le titulaire doit avoir un billet validé le jour du scan, à une date ultérieure à l'émission du coupon. Sur le port `8083`, récupérez le jeton avec `GET /coupons/{coupon_id}/qr` et l'en-tête `X-Client-Id`. Appelez ensuite `POST /coupons/{coupon_id}/utiliser` avec l'en-tête `X-Agent-Id`, le `jeton_qr` et le `produit_choisi` dans le corps JSON.
- Après le premier scan, consultez `GET /consommations` sur le port `8084` avec l'en-tête `X-Fournisseur-Id` du fournisseur concerné. Par défaut, une boisson est comptabilisée pour `fourn_boisson_1`. Le deuxième scan doit renvoyer `409` et le total doit rester identique. Les consommations sont regroupées selon le jour de l'événement dans le fuseau `Europe/Paris`.

## Arrêter les services

```sh
docker compose down
```

Cette commande conserve les volumes MongoDB et Redis. Après une modification du code, relancez `docker compose up -d --build --wait` pour reconstruire les images.

## Architecture et rapport

Catalogue, Réservations et Fournisseurs utilisent chacun une base MongoDB dédiée. Quiz utilise Redis pour ses données métier. Les événements à publier sont enregistrés avec les changements métier dans MongoDB ou Redis, puis transmis via le [module commun](services/commun/README.md).

La projection Elasticsearch de **Recherche** et le destinataire `application_spectateur` font partie de la conception. L'environnement Docker lance Catalogue, Réservations, Quiz et Fournisseurs.

| Élément | Contenu |
| --- | --- |
| [`index.qmd`](index.qmd) | Rapport Quarto, parcours métier et choix de stockage |
| [`DECISIONS.md`](DECISIONS.md) | Décisions de conception de l'équipe |
| [`2-services-commandes/`](2-services-commandes/) | Services et commandes métier |
| [`3-agregats/`](3-agregats/) | Agrégats, règles métier et modèles de données |
| [`4-evenements-replicas/`](4-evenements-replicas/) | Contrats d'événements et réplicas |
| [`5-projections/`](5-projections/) | Modèles et requêtes des projections Recherche et Fournisseurs |
| [`services/`](services/) | Services FastAPI et module commun |

Depuis la racine du dépôt, lancez `quarto preview index.qmd` avec [Quarto](https://quarto.org) pour ouvrir le rapport et le recharger pendant l'édition. La commande `quarto render index.qmd` régénère `index.html`.

## Équipe

- AMBIYA DIMAS Galystan
- DJANANG KOUOMOU Amery
- BARUA Nirzara
- NJONZO TAKAM Stephanie
- HIRA Amena
