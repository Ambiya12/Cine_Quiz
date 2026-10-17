# Décisions de conception

## Décisions à valider en équipe

| Sujet | Proposition | Décision de l'équipe |
|---|---|---|
| Billets | Un billet par place, avec son propre QR code ; chaque billet validé donne droit à son quiz ; un billet peut être transféré à un ami | |
| Modification de réservation | Non autorisée, par réalisme | |
| Annulation par le spectateur | Par l'acheteur, jusqu'à 30 minutes avant la séance, si aucun billet n'est validé ; remboursement total | |
| Annulation par le cinéma | Toujours possible ; toutes les réservations de la séance sont remboursées | |
| Tarifs | Plein, réduit et enfant ; le spectateur déclare son tarif à l'achat, l'agent vérifie le justificatif à l'entrée ; sans justificatif, pas d'entrée | |
| Accès au quiz | Billet validé à l'entrée, puis scan du QR code rotatif pendant le générique | |
| Déroulement du quiz | 2 heures pour le démarrer, 10 questions tirées parmi environ 40, 15 secondes par question, chronomètre côté serveur | |
| Points | Présence : 10 ; bonne réponse : 10 ; sans-faute : +20 ; badge débloqué : +50 | |
| Récompenses | Uniquement au comptoir : boisson (150 points), popcorn (200), menu (300) | |
| Coupons | Valables 30 jours, usage unique, rattachés au client, protégés par un jeton secret dans le QR code | |
| Coupon sans billet du jour | Autorisé pour l'instant (question ouverte) | |
| Classement | Retiré : peu adapté à une fréquentation de quelques séances par mois | |
| Notation des films | Retirée ; les recommandations s'appuient sur les films vus | |
| Amitié | Réciproque et immédiate, sans invitation à accepter | |
| Transfert de billet | Sans événement : « va voir » n'apparaît pas pour le nouveau titulaire | |
| Paiement et authentification | Hors périmètre, assurés par des prestataires simulés | |
| Identifiants | Préfixe en minuscules et tiret bas (`film_`, `res_`, `cpn_`…) ; exemples lisibles, générés automatiquement en réalité | |
| Projections | Fournisseurs et Recherche | |
| Confidentialité | Partage des films avec les amis désactivé par défaut, activable dans le profil ; les relations sont toujours enregistrées, le service Social filtre à l'affichage ; les recommandations restent anonymes | |
## Évolutions possibles

- **Place offerte** contre des points, utilisable à la caisse du cinéma.
- **Vente au guichet**, y compris la régularisation d'un tarif réduit sans justificatif.
- **Frais d'annulation**, par exemple 1 € par billet.
- **Notation des films**, pour affiner les recommandations.
- **Invitations d'amis**, à accepter ou refuser.
- **Événement de transfert de billet**, pour afficher « va voir » chez le nouveau titulaire.
- **Projection Statistiques** : taux de remplissage et taux d'absence par séance et par film, à partir de `seance.creee`, `reservation.confirmee`, `reservation.annulee` et `billet.valide`, pour aider le gérant à programmer ses films.
- **Projection Qualité du quiz** : repérer les questions trop faciles ou trop difficiles, en enrichissant `quiz.termine` du détail des réponses.
- **Plusieurs cinémas**, avec un agrégat `Cinema` et un `cinema_id` dans la Salle.
- **Âge minimum** pour les films interdits aux moins de 12 ou 16 ans, vérifié à la réservation.