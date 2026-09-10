import requests
import time
import csv
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

url_categories = "https://opentdb.com/api_category.php"
categories = requests.get(url_categories, verify=False).json()["trivia_categories"]
print("Nombre de catégories :", len(categories))

toutes_les_questions = []

for cat in categories:
    cat_id = cat["id"]
    cat_nom = cat["name"]

    url_count = f"https://opentdb.com/api_count.php?category={cat_id}"
    total_dispo = requests.get(url_count, verify=False).json()["category_question_count"]["total_question_count"]

    print(f"Catégorie : {cat_nom} ({total_dispo} questions au total)")

    if total_dispo == 0:
        continue

    token = requests.get("https://opentdb.com/api_token.php?command=request", verify=False).json()["token"]

    restant = total_dispo
    while restant > 0:
        a_demander = min(50, restant)  
        url_questions = f"https://opentdb.com/api.php?amount={a_demander}&category={cat_id}&token={token}"
        reponse = requests.get(url_questions, verify=False).json()
        code = reponse["response_code"]

        if code == 0:
            questions = reponse["results"]
            toutes_les_questions.extend(questions)
            restant -= len(questions)
            print(f"  {len(questions)} récupérées (total : {len(toutes_les_questions)}, reste {restant} sur cette catégorie)")
            time.sleep(5)
        elif code == 5:
            print("  Trop rapide, on patiente un peu avant de réessayer...")
            time.sleep(5)
        elif code == 4:
            print("  Token épuisé alors qu'il devrait rester des questions, on arrête cette catégorie ici.")
            break
        else:
            print(f"  Code retour inattendu ({code}), on arrête cette catégorie ici.")
            break

print(f"\nTotal final : {len(toutes_les_questions)} questions récupérées.")

if toutes_les_questions:
    colonnes = toutes_les_questions[0].keys()
    chemin_csv = "../data/bronze/questions_raw.csv"

    with open(chemin_csv, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=colonnes)
        writer.writeheader()
        writer.writerows(toutes_les_questions)

    print(f"Fichier sauvegardé : {chemin_csv}")
else:
    print("Aucune question récupérée, rien à sauvegarder.")