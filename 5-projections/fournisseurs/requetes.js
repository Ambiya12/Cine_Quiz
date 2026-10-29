// Projection Fournisseurs : ConsommationJournaliere
// Source : événements coupon.utilise émis par le service Quiz et coupons

// Exemple mongosh ; les transactions nécessitent un replica set MongoDB.
// 1 et 2. Enregistrer la réception et le compteur dans une seule transaction.
// Si une écriture échoue, aucune des deux n'est conservée.
const session = db.getMongo().startSession()
const projection = session.getDatabase(db.getName())

try {
  await session.withTransaction(async () => {
    const dejaTraite = await projection.evenements_recus.findOne({ _id: "evt_0912" })
    if (dejaTraite) return

    // _id reprend id_evenement ; les données utiles sont conservées à plat.
    await projection.evenements_recus.insertOne({
      _id: "evt_0912",
      type: "coupon.utilise",
      date: "2026-10-15T19:40:00Z",
      type_produit: "popcorn_moyen",
      fournisseur_id: "fourn_popcorn_1",
      film_origine_id: "film_dune2"
    })

    await projection.consommations.updateOne(
      { _id: "fourn_popcorn_1|2026-10-15" },
      {
        $set: { fournisseur_id: "fourn_popcorn_1", date: "2026-10-15" },
        $inc: { "produits.popcorn_moyen": 1, total: 1 }
      },
      { upsert: true }
    )
  })
} finally {
  await session.endSession()
}

// 3. Requête métier : total consommé par produit sur le mois d'octobre,
//    pour la facture du fournisseur.
db.consommations.aggregate([
  { $match: { fournisseur_id: "fourn_popcorn_1", date: { $gte: "2026-10-01", $lte: "2026-10-31" } } },
  { $project: { produits: { $objectToArray: "$produits" } } },
  { $unwind: "$produits" },
  { $group: { _id: "$produits.k", total: { $sum: "$produits.v" } } }
])

// 4. Évolution : une nouvelle question, sans toucher aux autres services.
//    « Quels films sont à l'origine du plus de coupons utilisés ? »
//    On la calcule à partir des événements conservés.
db.evenements_recus.aggregate([
  { $match: { type: "coupon.utilise", fournisseur_id: "fourn_popcorn_1" } },
  { $group: { _id: "$film_origine_id", coupons: { $sum: 1 } } },
  { $sort: { coupons: -1 } }
])
