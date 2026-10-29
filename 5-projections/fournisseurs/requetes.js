// Projection Fournisseurs : ConsommationJournaliere
// Source : événements coupon.utilise émis par le service Quiz et coupons

// 1. Conserver l'événement reçu.
//    L'identifiant de l'événement sert de _id : si le même événement arrive
//    deux fois, l'insertion échoue et on ne compte pas le coupon en double.
db.evenements_recus.insertOne({
  _id: "evt_0912",
  type: "coupon.utilise",
  date: "2026-10-15T19:40:00Z",
  type_produit: "popcorn_moyen",
  fournisseur_id: "fourn_popcorn_1",
  film_origine_id: "film_dune2"
})

// 2. Si l'insertion a réussi, incrémenter le compteur du jour.
//    upsert: true crée le document du jour s'il n'existe pas encore.
db.consommations.updateOne(
  { _id: "fourn_popcorn_1|2026-10-15" },
  {
    $set: { fournisseur_id: "fourn_popcorn_1", date: "2026-10-15" },
    $inc: { "produits.popcorn_moyen": 1, total: 1 }
  },
  { upsert: true }
)

// 3. Requête métier : total consommé par produit sur le mois d'octobre,
//    pour la facture du fournisseur.
db.consommations.aggregate([
  { $match: { fournisseur_id: "fourn_popcorn_1", date: { $gte: "2026-10-01", $lte: "2026-10-31" } } },
  { $project: { produits: { $objectToArray: "$produits" } } },
  { $unwind: "$produits" },
  { $group: { _id: "$produits.k", total: { $sum: "$produits.v" } } }
])

// 4. Évolution : une nouvelle question, sans toucher aux autres services.
//    « Quels films font gagner le plus de popcorns ? »
//    On la calcule à partir des événements conservés.
db.evenements_recus.aggregate([
  { $match: { type: "coupon.utilise", fournisseur_id: "fourn_popcorn_1" } },
  { $group: { _id: "$film_origine_id", coupons: { $sum: 1 } } },
  { $sort: { coupons: -1 } }
])
