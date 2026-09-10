"""Tests locaux sans inférence ; lancer avec unittest discover -s tests."""

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd
import requests

spec = importlib.util.spec_from_file_location(
    "enrich", Path(__file__).resolve().parents[1] / "scripts/enrich_with_llm.py"
)
pipeline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pipeline)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.row = {"question_id": "a" * 64, "question": "Who &amp; when?",
                    "correct_answer": "Leonardo da Vinci", "incorrect_answers": '["Other"]',
                    "category": "Art", "type": "multiple", "difficulty": "easy"}

    def test_normalization_is_strict(self):
        self.assertEqual(pipeline.normalize_answer(" Leonardo da Vinci. "), "leonardo da vinci")
        self.assertNotEqual(pipeline.normalize_answer("It is Leonardo da Vinci."), "leonardo da vinci")
        self.assertEqual(pipeline.normalize_answer("A &amp; B"), "a & b")

    def test_fallback_and_invalid_list(self):
        answers, warning = pipeline.parse_incorrect("['A &amp; B']")
        self.assertEqual(answers, ["A & B"])
        self.assertIsNotNone(warning)
        for bad in ('garbage', '{}', '[]', '[1]'):
            with self.assertRaises(ValueError):
                pipeline.parse_incorrect(bad)

    def test_success_and_deterministic_choices(self):
        payload = {"choices": [{"message": {"content": "Leonardo da Vinci."}}]}
        with patch.object(pipeline, "get_json", return_value=payload):
            first = pipeline.enrich(self.row, "test", Mock())
            second = pipeline.enrich(self.row, "test", Mock())
        self.assertTrue(first["ai_correct"])
        self.assertEqual(first["choices"], second["choices"])
        self.assertEqual(first["question_clean"], "Who & when?")

    def test_api_errors_have_null_score(self):
        for failure in (requests.Timeout("timeout"), requests.HTTPError("500"), ValueError("JSON")):
            with patch.object(pipeline, "get_json", side_effect=failure):
                result = pipeline.enrich(self.row, "test", Mock())
            self.assertEqual(result["status"], "error")
            self.assertIsNone(result["ai_correct"])
            self.assertGreaterEqual(result["response_time"], 0)

    def test_bad_row_does_not_call_model(self):
        with patch.object(pipeline, "get_json") as api:
            result = pipeline.enrich({**self.row, "incorrect_answers": "bad"}, "test", Mock())
        api.assert_not_called()
        self.assertIsNone(result["ai_correct"])

    def test_parquet_and_resume_compatibility(self):
        result = pipeline.enrich({**self.row, "incorrect_answers": "bad"}, "test", Mock())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "partial.parquet"
            pipeline.save_results([result], path)
            self.assertEqual(str(pd.read_parquet(path)["ai_correct"].dtype), "boolean")
            with patch.object(pipeline, "PARTIAL", path):
                saved = pipeline.load_results(False, pd.DataFrame([self.row]), "test")
                self.assertEqual(len(saved), 1)
                with self.assertRaisesRegex(ValueError, "paramètres"):
                    pipeline.load_results(False, pd.DataFrame([self.row]), "other-model")
                self.assertEqual(pipeline.load_results(True, pd.DataFrame([self.row]), "other"), [])

    def test_missing_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bronze.csv"
            path.write_text("question\nExample\n", encoding="utf-8")
            with patch.object(pipeline, "BRONZE", path):
                with self.assertRaisesRegex(ValueError, "Colonnes indispensables"):
                    pipeline.read_bronze()

    def test_unavailable_server_exits_cleanly(self):
        with patch.object(pipeline, "read_bronze", return_value=pd.DataFrame([self.row])), \
             patch.object(pipeline, "detect_model", side_effect=requests.ConnectionError("offline")), \
             patch("sys.argv", ["enrich_with_llm.py"]):
            self.assertEqual(pipeline.main(), 1)


if __name__ == "__main__":
    unittest.main()
