# Intégration des événements

Le [contrat d'événement](../../4-evenements-replicas/format-evenement.json) définit
le format et les garanties de livraison. L'objet `evenement` des fichiers
d'exemple correspond à l'enveloppe transmise. Les sections qui documentent les
réactions restent dans les fichiers de conception.

## Producteurs

Construire un `Evenement` depuis `services.commun.evenements` et sérialiser son
enveloppe avec `evenement.model_dump_json(exclude_unset=True)`. Réserver
`sequence_objet` et conserver le message JSON avec l'écriture métier dans
la même transaction MongoDB ou le même script Lua pour Quiz. En cas de nouvelle
tentative de publication, réutiliser cette chaîne JSON.

Chaque producteur fournit un objet compatible avec le protocole `BoiteEnvoi`.

| Méthode | Responsabilité du service |
|---|---|
| `prochain_message()` | Renvoyer le plus ancien JSON en attente ou `None`. Garder le message en attente tant que sa publication n'est pas confirmée. |
| `confirmer_publication(id_evenement)` | Marquer comme publié le message correspondant à cet identifiant. Le worker appelle cette méthode après l'envoi Redis. |

## Consommateurs

Chaque consommateur fournit un objet compatible avec le protocole `Reception`.
Il peut tester cet objet avec des exemples chargés via `Evenement.model_validate_json(message)`.

| Méthode | Responsabilité du service |
|---|---|
| `message_traite(id_evenement)` | Renvoyer le JSON déjà traité pour cet identifiant ou `None`. |
| `derniere_sequence(emetteur, type_objet, id_objet)` | Renvoyer la progression durable de l'objet pour le destinataire ou `0`. |
| `appliquer(evenement, message, sequence_precedente)` | Vérifier la progression attendue et appliquer l'effet métier. Enregistrer le message original et la nouvelle progression dans la même transaction MongoDB ou le même script Lua. Propager une exception en cas d'échec. |

`sequence_precedente` est la progression locale lue par le worker. La séquence
du nouvel événement peut être supérieure à `sequence_precedente + 1` si le journal
confirme que les faits intermédiaires visaient d'autres destinataires.
Le worker acquitte après succès. Les traitements qui affectent plusieurs
réservations ou tentatives suivent la reprise durable décrite dans
[`seance.annulee`](../../4-evenements-replicas/evenements/seance.annulee.json).

Chaque application dispose de `app.state.redis`, de `app.state.redis_prefix` et,
pour les services MongoDB, de `app.state.db` pendant son cycle de vie FastAPI.
