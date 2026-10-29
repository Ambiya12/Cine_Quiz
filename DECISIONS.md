# Décisions de conception

## Périmètre du dépôt

Trois services métier sont décrits : Catalogue, Réservations et Quiz et coupons.
Recherche et Fournisseurs portent les deux projections de lecture. L'identité
des clients est fournie par le prestataire d'authentification simulé ; la gestion
des profils reste hors périmètre.

## Décisions à valider en équipe

| Sujet | Proposition | Décision de l'équipe |
|---|---|---|
| Billets | Un billet par place, avec son propre QR code ; chaque billet validé donne droit à son quiz ; un billet peut être transféré à un autre spectateur | |
| Modification de réservation | Non autorisée, par réalisme | |
| Annulation par le spectateur | Par l'acheteur, plus de 30 minutes avant la séance, si aucun billet n'est validé ; remboursement total | |
| Annulation par le cinéma | Avant la fin prévue, y compris pendant la séance ; remboursement total des réservations encore confirmées, même après une entrée | |
| Montant et remboursement | montant conserve le prix d'achat ; le remboursement est une entrée négative ajoutée aux paiements ; leur somme donne le net payé, sans autre champ stocké | |
| Annulation répétée | Retourner « déjà annulée » après contrôle du demandeur, sans autre remboursement ni nouvel événement | |
| Quiz après annulation cinéma | À réception de seance.annulee, annuler les tentatives non terminées ; conserver les résultats terminés et les coupons déjà émis | |
| Tarifs | Plein, réduit et enfant ; le spectateur déclare son tarif à l'achat, l'agent vérifie le justificatif à l'entrée ; sans justificatif, pas d'entrée | |
| Accès au quiz | Titulaire authentifié correspondant au client_id de la tentative ; code signé encore valide de la même séance, scanné entre le début du générique inclus et la fin de séance exclue | |
| Déroulement du quiz | 2 heures pour démarrer ; ensuite 10 questions de 15 secondes, au plus 150 secondes après le démarrage ; les absences sont fausses et le serveur avance même sans réponse | |
| Choix du quiz | Identifiants de choix stables ; questions et ordre des choix figés au démarrage ; correction privée côté serveur ; aucune remise à zéro lors d'une reconnexion | |
| Récompense du quiz | Aucun coupon de 0 à 7/10 ; un tirage à 50 % pour 8 ou 9/10 ; coupon garanti à 10/10 ; un popcorn moyen ou une boisson au choix à une visite ultérieure | |
| Attribution unique | Une décision enregistrée par tentative, même perdante ; un rejeu ne répète ni le tirage ni la création du coupon ; au plus un coupon par tentative | |
| Mises à jour par événement | Deux opérations explicites : remplacer un champ par sa valeur complète, ou ajouter un élément à une liste ; aucun chemin à l'intérieur des tableaux | |
| Événements reçus deux fois | Chaque service mémorise ses id_evenement traités ; les modifications et cet enregistrement sont validés ensemble ; une répétition ne produit aucun effet métier supplémentaire | |
| Accès aux données | Définir une table, collection ou clé pour chaque lecture ; cinq tables Cassandra pour Réservation, avec acheteur distinct du titulaire ; réutiliser billets_par_seance pour les annulations de séance | |
| Vente unique d'une place | Pour seance_id et place, une seule réservation active ; contrôle et attribution atomiques dans billets_par_seance ; le blocage Redis temporaire ne suffit pas à confirmer la vente | |
| Conservation Redis | Détail du quiz nettoyable après sa fin ; résumés de récompense, coupons et index client durables ; AOF sur disque conservé et sans éviction automatique pour Redis Quiz | |
| Coupons | Valables 30 jours, usage unique, rattachés au client et à leur tentative d'origine ; produit choisi au comptoir à une visite ultérieure ; protégés par un jeton secret dans le QR code | |
| Coupon sans billet du jour | Autorisé pour l'instant (question ouverte) | |
| Transfert de billet | Le titulaire change dans Réservations ; aucun événement de transfert n'est défini dans le périmètre actuel | |
| Paiement et authentification | Hors périmètre, assurés par des prestataires simulés | |
| Identifiants | Préfixe en minuscules et tiret bas (`film_`, `res_`, `cpn_`…) ; exemples lisibles, générés automatiquement en réalité | |
| Projections | Fournisseurs et Recherche | |

## Évolutions possibles

- **Vente au guichet**, y compris la régularisation d'un tarif réduit sans justificatif.
- **Frais d'annulation**, par exemple 1 € par billet.
- **Projection Statistiques** : taux de remplissage et taux d'absence par séance et par film, à partir de `seance.creee`, `reservation.confirmee`, `reservation.annulee` et `billet.valide`, pour aider le gérant à programmer ses films.
- **Projection Qualité du quiz** : repérer les questions trop faciles ou trop difficiles, en enrichissant `quiz.termine` du détail des réponses.
- **Plusieurs cinémas**, avec un agrégat `Cinema` et un `cinema_id` dans la Salle.
- **Âge minimum** pour les films interdits aux moins de 12 ou 16 ans, vérifié à la réservation.
