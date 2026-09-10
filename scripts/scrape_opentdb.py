import csv
import json
import time
import requests
from pathlib import Path
from datetime import datetime, timezone


BASE_URL = "https://opentdb.com"
ROOT = Path(__file__).resolve().parents[1]
OUTPUT_FILE = ROOT / "data/bronze/questions_raw.csv"

# OpenTDB limite actuellement les appels à 1 requête / 5 secondes.
SLEEP_TIME = 5.2


def get_json(url, params=None):
    """Effectue une requête en respectant le rate limit OpenTDB."""
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def get_token():
    print("Récupération d'un session token...")

    data = get_json(
        f"{BASE_URL}/api_token.php",
        params={"command": "request"},
    )

    if data["response_code"] != 0:
        raise RuntimeError(f"Impossible d'obtenir un token : {data}")

    time.sleep(SLEEP_TIME)

    return data["token"]


def get_categories():
    data = get_json(f"{BASE_URL}/api_category.php")
    time.sleep(SLEEP_TIME)
    return data["trivia_categories"]


def get_category_count(category_id):
    data = get_json(
        f"{BASE_URL}/api_count.php",
        params={"category": category_id},
    )

    time.sleep(SLEEP_TIME)

    return data["category_question_count"]["total_question_count"]


def scrape_all_questions():
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    token = get_token()
    categories = get_categories()

    all_questions = []

    for category in categories:
        category_id = category["id"]
        category_name = category["name"]

        total_questions = get_category_count(category_id)

        print(
            f"\n[{category_id}] {category_name} "
            f"-> {total_questions} questions"
        )

        collected = 0

        while collected < total_questions:
            amount = min(50, total_questions - collected)

            data = get_json(
                f"{BASE_URL}/api.php",
                params={
                    "amount": amount,
                    "category": category_id,
                    "token": token,
                },
            )

            response_code = data["response_code"]

            if response_code == 0:
                questions = data["results"]

                fetched_at = datetime.now(timezone.utc).isoformat()

                for question in questions:
                    all_questions.append(
                        {
                            "category": question["category"],
                            "type": question["type"],
                            "difficulty": question["difficulty"],
                            "question": question["question"],
                            "correct_answer": question["correct_answer"],

                            # On garde la liste sous forme JSON
                            # pour ne pas perdre l'information.
                            "incorrect_answers": json.dumps(
                                question["incorrect_answers"],
                                ensure_ascii=False,
                            ),

                            # Métadonnées Bronze
                            "source": "OpenTDB",
                            "category_id": category_id,
                            "fetched_at": fetched_at,
                        }
                    )

                collected += len(questions)

                print(
                    f"   {collected}/{total_questions} récupérées"
                )

            elif response_code == 5:
                # Rate limit
                print("Rate limit atteint, nouvelle tentative...")
                time.sleep(6)
                continue

            else:
                raise RuntimeError(
                    f"Erreur OpenTDB : response_code={response_code}"
                )

            time.sleep(SLEEP_TIME)

    return all_questions


def save_csv(questions):
    fieldnames = [
        "category",
        "type",
        "difficulty",
        "question",
        "correct_answer",
        "incorrect_answers",
        "source",
        "category_id",
        "fetched_at",
    ]

    with open(
        OUTPUT_FILE,
        "w",
        newline="",
        encoding="utf-8",
    ) as csvfile:

        writer = csv.DictWriter(
            csvfile,
            fieldnames=fieldnames,
        )

        writer.writeheader()
        writer.writerows(questions)

    print(f"\n✅ {len(questions)} questions sauvegardées")
    print(f"📁 {OUTPUT_FILE}")


if __name__ == "__main__":
    questions = scrape_all_questions()
    save_csv(questions)
