"""Enrichissement Silver local ; exécuter avec --help pour les options."""

from __future__ import annotations

import argparse
import ast
import hashlib
import html
import json
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
BRONZE = ROOT / "data/bronze/questions_raw.csv"
SILVER = ROOT / "data/silver/questions_enriched.parquet"
PARTIAL = SILVER.with_name("questions_enriched_partial.parquet")
BASE_URL = "http://localhost:1234/v1"
MODEL = None
LIMIT = None
TEMPERATURE = 0
MAX_TOKENS = 50
TIMEOUT = 300
CHECKPOINT_EVERY = 10
PROMPT_VERSION = "v1_exact_choice"


def normalize_answer(value: str) -> str:
    """Comparaison stricte, hors casse, espaces et un point final."""
    return html.unescape(value).strip().lower().removesuffix(".").rstrip()


def parse_incorrect(value: str) -> tuple[list[str], str | None]:
    warning = None
    try:
        answers = json.loads(value)
    except (ValueError, TypeError):
        try:
            answers = ast.literal_eval(value)
            warning = "Liste non JSON récupérée avec ast.literal_eval."
        except (ValueError, SyntaxError, TypeError) as exc:
            raise ValueError("incorrect_answers : liste JSON invalide") from exc
    if not isinstance(answers, list) or not answers:
        raise ValueError("incorrect_answers doit être une liste non vide")
    if any(not isinstance(a, str) or not a.strip() for a in answers):
        raise ValueError("incorrect_answers contient une réponse vide ou non textuelle")
    return [html.unescape(a).strip() for a in answers], warning


def read_bronze() -> pd.DataFrame:
    path = BRONZE
    if not path.exists():
        raise ValueError(f"Bronze introuvable : {BRONZE}")
    print(f"Bronze : {path}")
    frame = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    missing = {"question", "correct_answer", "incorrect_answers"} - set(frame.columns)
    if missing:
        raise ValueError(f"Colonnes indispensables absentes : {', '.join(sorted(missing))}")
    for column in ("category", "type", "difficulty"):
        if column not in frame:
            frame[column] = ""
    # Inclure tous les choix évite de réutiliser un résultat si les distracteurs changent.
    frame["question_id"] = [
        hashlib.sha256(json.dumps(
            [row[c] for c in ("category", "question", "correct_answer", "incorrect_answers")],
            ensure_ascii=False,
        ).encode("utf-8")).hexdigest()
        for row in frame.to_dict("records")
    ]
    duplicates = int(frame.duplicated("question_id").sum())
    if duplicates:
        print(f"Doublons identiques ignorés : {duplicates}")
    return frame.drop_duplicates("question_id").reset_index(drop=True)


def get_json(session: requests.Session, method: str, endpoint: str, **kwargs) -> dict:
    response = session.request(
        method, f"{BASE_URL}{endpoint}", allow_redirects=False, **kwargs
    )
    response.raise_for_status()
    if response.is_redirect:
        raise ValueError("Redirection refusée : les requêtes doivent rester locales")
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("L'API a renvoyé un JSON inattendu")
    return payload


def detect_model(session: requests.Session, forced: str | None) -> str:
    payload = get_json(session, "GET", "/models", timeout=10)
    entries = payload.get("data")
    if not isinstance(entries, list):
        raise ValueError("/models : champ data invalide")
    ids = [m["id"] for m in entries if isinstance(m, dict) and isinstance(m.get("id"), str)]
    if forced:
        if forced not in ids:
            raise ValueError(f"Modèle demandé absent de /models : {forced}")
        return forced
    ids = [model for model in ids if "embed" not in model.lower()]
    if not ids:
        raise ValueError("Aucun modèle de conversation disponible")
    return ids[0]


def make_prompt(question: str, choices: list[str]) -> str:
    return (
        "Tu participes à un benchmark de culture générale.\n\n"
        "Réponds uniquement avec l'une des réponses proposées.\n"
        "Ne donne aucune explication.\n"
        "Ne donne aucune phrase supplémentaire.\n"
        "Recopie exactement la réponse choisie.\n\n"
        f"Question :\n{question}\n\nRéponses possibles :\n"
        + "\n".join(f"- {choice}" for choice in choices)
        + "\n\nRéponse :"
    )


def enrich(row: dict, model: str, session: requests.Session) -> dict:
    result = {**row, "model": model, "prompt_version": PROMPT_VERSION,
              "temperature": TEMPERATURE, "max_tokens": MAX_TOKENS,
              "benchmark_timestamp": datetime.now(timezone.utc).isoformat(),
              "question_clean": html.unescape(row["question"]).strip(),
              "correct_answer_clean": html.unescape(row["correct_answer"]).strip(),
              "incorrect_answers_clean": None, "choices": None, "prompt": None,
              "ai_answer": None, "ai_correct": None, "response_time": None,
              "status": "error", "error": None, "cleaning_warning": None}
    started = None
    try:
        if not result["question_clean"] or not result["correct_answer_clean"]:
            raise ValueError("Question ou bonne réponse vide")
        incorrect, warning = parse_incorrect(row["incorrect_answers"])
        result["cleaning_warning"] = warning
        result["incorrect_answers_clean"] = json.dumps(incorrect, ensure_ascii=False)
        choices = [result["correct_answer_clean"], *incorrect]
        random.Random(int(row["question_id"], 16)).shuffle(choices)
        result["choices"] = json.dumps(choices, ensure_ascii=False)
        result["prompt"] = make_prompt(result["question_clean"], choices)
        started = time.perf_counter()
        payload = get_json(session, "POST", "/chat/completions", timeout=TIMEOUT, json={
            "model": model, "messages": [{"role": "user", "content": result["prompt"]}],
            "temperature": TEMPERATURE, "max_tokens": MAX_TOKENS, "stream": False,
        })
        result["response_time"] = time.perf_counter() - started
        answer = payload["choices"][0]["message"]["content"]
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("Réponse du modèle vide ou non textuelle")
        result["ai_answer"] = answer
        if payload["choices"][0].get("finish_reason") == "length":
            raise ValueError("Réponse tronquée : limite max_tokens atteinte")
        result["ai_correct"] = normalize_answer(answer) == normalize_answer(result["correct_answer_clean"])
        result["status"] = "success"
    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if started is not None and result["response_time"] is None:
            result["response_time"] = time.perf_counter() - started
    return result


def save_results(results: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(results)
    frame["ai_correct"] = frame["ai_correct"].astype("boolean")
    frame["response_time"] = pd.to_numeric(frame["response_time"]).astype("Float64")
    # Schéma stable même si les premières lignes ne contiennent que des erreurs.
    for column in frame.columns.difference(["ai_correct", "response_time", "temperature", "max_tokens"]):
        frame[column] = frame[column].astype("string")
    temporary = path.with_suffix(".tmp.parquet")
    frame.to_parquet(temporary, index=False, engine="pyarrow")
    temporary.replace(path)


def load_results(restart: bool, source: pd.DataFrame, model: str) -> list[dict]:
    path = PARTIAL if PARTIAL.exists() else SILVER
    if restart or not path.exists():
        return []
    saved = pd.read_parquet(path)
    required = {"question_id", "model", "prompt_version", "temperature", "max_tokens", "status"}
    if not required.issubset(saved.columns):
        raise ValueError("Checkpoint incompatible ; utiliser --restart")
    compatible = ((saved["model"] == model) & (saved["prompt_version"] == PROMPT_VERSION)
                  & (saved["temperature"] == TEMPERATURE) & (saved["max_tokens"] == MAX_TOKENS))
    if not compatible.fillna(False).all():
        raise ValueError("Modèle ou paramètres différents du checkpoint ; utiliser --restart")
    saved = saved[saved["question_id"].isin(source["question_id"])]
    return saved.drop_duplicates("question_id").to_dict("records")


def print_summary(results: list[dict], model: str) -> None:
    frame = pd.DataFrame(results)
    valid = frame[frame["status"] == "success"]
    correct = int(valid["ai_correct"].sum())
    print("\n===========================\nBENCHMARK TERMINÉ\n===========================")
    print(f"Modèle : {model}\nQuestions traitées : {len(frame)}")
    print(f"Réponses valides : {len(valid)}\nErreurs : {len(frame) - len(valid)}")
    print(f"Bonnes réponses : {correct}\nRéponses incorrectes : {len(valid) - correct}")
    if len(valid):
        print(f"Accuracy : {100 * correct / len(valid):.2f} %")
        print(f"Temps moyen : {valid['response_time'].mean():.2f} s")
        print(f"Temps médian : {valid['response_time'].median():.2f} s")
    else:
        print("Accuracy et temps des réponses valides : non disponibles")
    print(f"Silver : {SILVER}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=LIMIT, help="Nombre total de questions visées")
    parser.add_argument("--model", default=MODEL, help="ID du modèle LM Studio")
    parser.add_argument("--restart", action="store_true", help="Ignorer les résultats précédents")
    parser.add_argument("--retry-errors", action="store_true", help="Retenter les lignes en erreur")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit doit être supérieur à zéro")
    try:
        source = read_bronze()
        if source.empty:
            raise ValueError("Le fichier Bronze ne contient aucune question")
        with requests.Session() as session:
            session.trust_env = False  # Pas de proxy système : requêtes exclusivement locales.
            try:
                model = detect_model(session, args.model)
            except (requests.RequestException, ValueError) as exc:
                raise ValueError(
                    f"LM Studio indisponible ou modèle absent : {exc}\n"
                    "Chargez Ministral, puis démarrez le serveur dans Developer "
                    f"et vérifiez {BASE_URL}/models."
                ) from exc
            print(f"LM Studio détecté\nModèle : {model}")
            results = load_results(args.restart, source, model)
            selected = source.head(args.limit) if args.limit is not None else source
            target_ids = set(selected["question_id"])
            if args.retry_errors:
                results = [r for r in results if r["status"] == "success" or r["question_id"] not in target_ids]
            done = {r["question_id"] for r in results}
            pending = selected[~selected["question_id"].isin(done)]
            print(f"Questions visées : {len(selected)} ; restantes : {len(pending)}")
            try:
                for index, row in enumerate(pending.to_dict("records"), 1):
                    print(f"\n[{index}/{len(pending)}] {html.unescape(row['question'])}", flush=True)
                    result = enrich(row, model, session)
                    results.append(result)
                    label = "ERREUR" if result["status"] == "error" else ("OK" if result["ai_correct"] else "FAUX")
                    print(f"{label} : {result['error'] or result['ai_answer']}")
                    print(f"Attendu : {result['correct_answer_clean']}")
                    if result["response_time"] is not None:
                        print(f"Temps : {result['response_time']:.2f} s", flush=True)
                    if index % CHECKPOINT_EVERY == 0:
                        save_results(results, PARTIAL)
            finally:
                if results:
                    save_results(results, PARTIAL)
            save_results(results, SILVER)
            print_summary([r for r in results if r["question_id"] in target_ids], model)
            print(f"Résultats conservés dans Silver (toutes exécutions compatibles) : {len(results)}")
        return 0
    except KeyboardInterrupt:
        print("\nInterruption : résultats terminés sauvegardés. Relancez pour reprendre.")
        return 130
    except (ValueError, OSError, requests.RequestException) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
