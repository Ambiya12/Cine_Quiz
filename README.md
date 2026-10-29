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
4. **Générique** : le titulaire scanne le QR code de sa séance pendant le générique ; il dispose de 2 heures pour démarrer.
5. **Quiz** : 10 questions, 15 secondes chacune, au plus 150 secondes après le démarrage ; les choix mélangés gardent leurs identifiants et leur ordre après reconnexion.
6. **Récompense** : une seule décision par quiz ; aucun coupon de 0 à 7/10, un tirage à 50 % pour 8 ou 9/10, coupon garanti à 10/10.
7. **Prochaine visite** : le client choisit un popcorn ou une boisson au comptoir ; le coupon est valable 30 jours et utilisable une seule fois ; le fournisseur reçoit le total du produit réellement remis.

## Les services

| Service | Base | Agrégats |
|---|---|---|
| Catalogue | MongoDB | Film, Salle, Séance |
| Réservations | Cassandra (+ Redis) | Réservation |
| Quiz et coupons | Redis | Tentative, Coupon |
| Recherche | Elasticsearch | Aucun (projection) |
| Fournisseurs | MongoDB | Aucun (projection) |

Le périmètre comprend trois services métier et deux services de projection.
Le service Quiz et coupons utilise l'identifiant `quiz` dans les contrats et les
chemins du dépôt. Les identifiants des clients proviennent du prestataire
d'authentification simulé ; la gestion des profils reste hors périmètre.

Le dépôt décrit la conception ; il ne contient pas encore une application exécutable.

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
