# Décisions de conception

## Périmètre du dépôt

Trois services métier sont décrits : Catalogue, Réservations et Quiz et coupons.
Recherche et Fournisseurs portent les deux projections de lecture. L'identité
des clients est fournie par le prestataire d'authentification simulé ; la gestion
des profils reste hors périmètre.

## Décisions à valider en équipe

| Sujet | Proposition | Décision de l'équipe |
|---|---|---|
| Billets | Un billet par place, avec son propre QR code ; chaque billet validé donne droit à son quiz ;  | Validée par l'équipe|
| Modification de réservation | Non autorisée, par réalisme | Validée par l'équipe|
| Annulation par le spectateur | Si l'acheteur annule une réservation 1h avant le début de la séance alors le remboursement sera attribué. | Validée par l'équipe|
| Annulation par le cinéma | Avant la fin prévue, y compris pendant la séance ; remboursement total des réservations encore confirmées, même après une entrée | Validée par l'équipe  |
| Quiz après annulation cinéma | À réception de seance.annulee, annuler les tentatives non terminées ; conserver les résultats terminés et les coupons déjà émis | Validée par l'équipe|
| Tarifs | Plein, réduit et enfant ; le spectateur déclare son tarif à l'achat, l'agent vérifie le justificatif à l'entrée ; sans justificatif, pas d'entrée | Validée par l'équipe|
| Accès au quiz | Titulaire authentifié correspondant au client_id de la tentative ; QR Code pour le quiz imprimé sur le billet. | Validée par l'équipe |
| Déroulement du quiz | 2 heures pour démarrer ; ensuite 10 questions de 15 secondes, au plus 150 secondes après le démarrage ; les absences sont fausses et le serveur avance même sans réponse |Validée par l'équipe |
| Choix du quiz | Identifiants de choix stables ; questions et ordre des choix figés au démarrage ; correction privée côté serveur ; aucune remise à zéro lors d'une reconnexion |Validée par l'équie |
| Récompense du quiz | Aucun coupon de 0 à 7/10 ; coupon garanti à 08/10 ; un popcorn moyen ou une boisson au choix à une visite ultérieure. |Validée par l'équipe |
| Attribution unique | Une décision enregistrée par tentative, même perdante ; un rejeu ne répète ni le tirage ni la création du coupon ; au plus un coupon par tentative | Validée par l'équipe|
| Mises à jour par événement | Deux opérations explicites : remplacer un champ par sa valeur complète, ou ajouter un élément à une liste ; aucun chemin à l'intérieur des tableaux | Préférence pour Redis avec List et Stream pour la gestion des évènements.|
| Événements reçus deux fois | Chaque service mémorise ses id_evenement traités ; les modifications et cet enregistrement sont validés ensemble ; une répétition ne produit aucun effet métier supplémentaire |Validée par l'équipe|
| Accès aux données | Définir une table, collection ou clé pour chaque lecture ; cinq tables MongoDB pour Réservation, avec acheteur distinct du titulaire ; réutiliser billets_par_seance pour les annulations de séance |Validée par l'équipe|
| Vente unique d'une place | Pour seance_id et place, une seule réservation active ; contrôle et attribution atomiques dans billets_par_seance ; le blocage Redis temporaire ne suffit pas à confirmer la vente | Validé par l'équipe|
| Conservation Redis | Nettoyage des coupons et historique des récompeneses obtenues par les spectateurs et index client durables ; AOF sur disque conservé et sans éviction automatique pour Redis Quiz |Validé par l'équipe |
| Coupons | Valables 30 jours, usage unique, rattachés au client et à leur tentative d'origine ; produit choisi au comptoir à une visite ultérieure ; protégés par un jeton secret dans le QR code | Validée par l'équipe  |
| Paiement et authentification | Hors périmètre, assurés par des prestataires simulés | Validée par l'équipe|
| Identifiants | Préfixe en minuscules et tiret bas (`film_`, `res_`, `cpn_`…) ; exemples lisibles, générés automatiquement en réalité | |
| Projections | Fournisseurs et Recherche (avec ElasticSearch) | |

## Évolutions possibles

- **Vente au guichet**, y compris la régularisation d'un tarif réduit sans justificatif.
- **Frais d'annulation**, par exemple 1 € par billet.
- **Projection Statistiques** : taux de remplissage et taux d'absence par séance et par film, à partir de `seance.creee`, `reservation.confirmee`, `reservation.annulee` et `billet.valide`, pour aider le gérant à programmer ses films.
- **Projection Qualité du quiz** : repérer les questions trop faciles ou trop difficiles, en enrichissant `quiz.termine` du détail des réponses.
- **Plusieurs cinémas**, avec un agrégat `Cinema` et un `cinema_id` dans la Salle.
- **Âge minimum** pour les films interdits aux moins de 12 ou 16 ans, vérifié à la réservation.
