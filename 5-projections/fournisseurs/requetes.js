// Projection Fournisseurs : ConsommationJournaliere
// Source : événements coupon.utilise émis par le service quiz_fidelite.
// Exemples prévus pour mongosh et un déploiement MongoDB en replica set,
// nécessaire pour garantir l'atomicité de la transaction.

const FUSEAU_CINEMA = "Europe/Paris";
const DEVISE = "EUR";

// Transforme un instant ISO 8601 en jour civil du cinéma.
// Exemple : 2026-10-15T23:30:00Z devient le 2026-10-16 à Paris
// si l'heure locale est déjà passée à minuit.
function obtenirJourLocal(dateEvenement) {
  const date = new Date(dateEvenement);
  if (Number.isNaN(date.getTime())) {
    throw new Error(`Date d'événement invalide : ${dateEvenement}`);
  }

  const parties = new Intl.DateTimeFormat("en-CA", {
    timeZone: FUSEAU_CINEMA,
    year: "numeric",
    month: "2-digit",
    day: "2-digit"
  }).formatToParts(date);

  const valeurs = Object.fromEntries(parties.map(({ type, value }) => [type, value]));
  return `${valeurs.year}-${valeurs.month}-${valeurs.day}`;
}

// L'argument est l'objet evenement de l'enveloppe officielle,
// et non l'enveloppe complète { evenement, reactions }.
function traiterCouponUtilise(evenement) {
  if (evenement.type !== "coupon.utilise") {
    throw new Error(`Type d'événement inattendu : ${evenement.type}`);
  }

  const idEvenement = evenement.id_evenement;
  const fournisseurId = evenement.contexte?.fournisseur_id;
  const typeProduit = evenement.contexte?.type;
  const filmOrigineId = evenement.contexte?.film_origine_id ?? null;
  const valeurUnitaireCentimes = evenement.contexte?.valeur_unitaire_centimes;

  if (!idEvenement || !fournisseurId || !typeProduit) {
    throw new Error("L'événement doit contenir id_evenement, fournisseur_id et type produit.");
  }
  if (!Number.isSafeInteger(valeurUnitaireCentimes) || valeurUnitaireCentimes <= 0) {
    throw new Error("valeur_unitaire_centimes doit être un entier strictement positif.");
  }

  const dateLocale = obtenirJourLocal(evenement.date);
  const idConsommation = `${fournisseurId}|${dateLocale}`;
  const cheminProduit = `produits.${typeProduit}`;

  const session = db.getMongo().startSession();
  try {
    const dbSession = session.getDatabase("fournisseurs");

    session.withTransaction(() => {
      // L'insertion et les incréments sont validés ensemble.
      // Si l'événement existe déjà, l'erreur de clé dupliquée annule
      // la transaction : aucun compteur n'est incrémenté une seconde fois.
      dbSession.evenements_recus.insertOne({
        _id: idEvenement,
        evenement: evenement
      });

      dbSession.consommations.updateOne(
        { _id: idConsommation },
        {
          $setOnInsert: {
            fournisseur_id: fournisseurId,
            date_locale: dateLocale,
            fuseau_horaire: FUSEAU_CINEMA,
            devise: DEVISE
          },
          $inc: {
            [`${cheminProduit}.quantite`]: 1,
            [`${cheminProduit}.valeur_totale_centimes`]: valeurUnitaireCentimes,
            quantite_totale: 1,
            valeur_totale_centimes: valeurUnitaireCentimes
          }
        },
        { upsert: true }
      );
    });
  } catch (erreur) {
    // Une clé dupliquée signifie que cet événement a déjà été traité.
    // Les autres erreurs doivent remonter pour permettre une nouvelle tentative.
    if (erreur.code === 11000) {
      return { statut: "deja_traite", id_evenement: idEvenement };
    }
    throw erreur;
  } finally {
    session.endSession();
  }

  return {
    statut: "traite",
    id_evenement: idEvenement,
    fournisseur_id: fournisseurId,
    date_locale: dateLocale,
    film_origine_id: filmOrigineId
  };
}

// Exemple d'événement conforme à l'enveloppe officielle.
// Le champ valeur_unitaire_centimes est une évolution à ajouter à
// evenement.contexte dans le contrat coupon.utilise.
const exempleEvenement = {
  id_evenement: "evt_0912",
  type: "coupon.utilise",
  date: "2026-10-15T19:40:00Z",
  emetteur: "quiz_fidelite",
  recepteurs: ["fournisseurs"],
  type_objet: "Coupon",
  id_objet: "cpn_55",
  modifications: [
    { donnee: "utilise", nouvelle_valeur: true },
    { donnee: "utilise_le", nouvelle_valeur: "2026-10-15T19:40:00Z" }
  ],
  contexte: {
    type: "popcorn_moyen",
    fournisseur_id: "fourn_popcorn_1",
    film_origine_id: "film_dune2",
    valeur_unitaire_centimes: 450
  }
};

// À appeler une seule fois à la réception du message Redis Stream :
// traiterCouponUtilise(exempleEvenement);

// 1. Requête métier : quantités et valeur commerciale distribuées
//    par produit sur le mois d'octobre pour un fournisseur.
db.consommations.aggregate([
  {
    $match: {
      fournisseur_id: "fourn_popcorn_1",
      date_locale: { $gte: "2026-10-01", $lte: "2026-10-31" }
    }
  },
  { $project: { produits: { $objectToArray: "$produits" } } },
  { $unwind: "$produits" },
  {
    $group: {
      _id: "$produits.k",
      quantite: { $sum: "$produits.v.quantite" },
      valeur_totale_centimes: { $sum: "$produits.v.valeur_totale_centimes" }
    }
  },
  { $sort: { _id: 1 } }
]);

// 2. Nouvelle analyse : quels films sont à l'origine de la plus grande
//    valeur commerciale de produits distribués ? Les événements conservés
//    permettent cette analyse sans ajouter film_origine_id à la projection
//    journalière agrégée.
db.evenements_recus.aggregate([
  {
    $match: {
      "evenement.type": "coupon.utilise",
      "evenement.contexte.fournisseur_id": "fourn_popcorn_1"
    }
  },
  {
    $group: {
      _id: "$evenement.contexte.film_origine_id",
      coupons: { $sum: 1 },
      valeur_totale_centimes: {
        $sum: "$evenement.contexte.valeur_unitaire_centimes"
      }
    }
  },
  { $sort: { valeur_totale_centimes: -1 } }
]);
