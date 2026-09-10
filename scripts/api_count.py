import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# On récupère la liste des catégories
url_categories = "https://opentdb.com/api_category.php"
reponse = requests.get(url_categories, verify=False)
categories = reponse.json()["trivia_categories"]

# Pour chaque catégorie, on demande le nombre total de questions disponibles
for cat in categories:
    cat_id = cat["id"]
    cat_nom = cat["name"]
    url_count = f"https://opentdb.com/api_count.php?category={cat_id}"
    reponse = requests.get(url_count, verify=False).json()
    total_dispo = reponse["category_question_count"]["total_question_count"]
    print(f"{cat_nom} : {total_dispo} questions disponibles")