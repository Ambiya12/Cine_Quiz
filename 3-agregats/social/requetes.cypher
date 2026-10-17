// Requêtes du service Social (Neo4j)
// Les noms des propriétés correspondent aux agrégats : client_id, film_id, partager_activite.

// 1. « Tes amis vont voir » : les films que mes amis vont voir,
//    uniquement pour les amis qui ont activé le partage.
MATCH (moi:Client {client_id: $client_id})-[:AMI_DE]-(ami:Client)-[:VA_VOIR]->(f:Film)
WHERE ami.partager_activite = true
RETURN ami.pseudo, f.titre;

// 2. Recommandation anonyme : « les spectateurs qui ont vu ce film ont aussi vu… »
//    Elle utilise les films vus de tous les spectateurs, quel que soit leur réglage,
//    mais n'affiche que des titres et des nombres, jamais de noms.
MATCH (f:Film {film_id: $film_id})<-[:A_VU]-(c:Client)-[:A_VU]->(autre:Film)
WHERE autre <> f
RETURN autre.titre, count(DISTINCT c) AS spectateurs
ORDER BY spectateurs DESC
LIMIT 5;