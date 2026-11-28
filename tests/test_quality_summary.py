import os
import tempfile
import unittest
from unittest.mock import patch

from verify_questions import quality_summary


class LatestResultsTest(unittest.TestCase):
    def test_latest_run_counts_only_assertion_results(self):
        with tempfile.TemporaryDirectory() as directory:
            os.mkdir(os.path.join(directory, "runs"))
            with open(os.path.join(directory, "runs", "latest.md"), "w", encoding="utf-8") as run:
                run.write(
                    "## 斷言結果\n"
                    "| C1 accuracy | 準確性 | PASS | 0 |\n"
                    "| C2 temporal | 一致性 | **FAIL** | 2 |\n"
                    "| C3 validity | 有效性 | INCONCLUSIVE | PASS is not established |\n"
                    "## 抽查\n| C3 | PASS |\n"
                )
            card = (
                "status: passing\n## 歷次結果\n"
                "| 2026-01-01 | PASS 3／3 | — |\n"
                "| 2026-01-02 | FAIL | [run](../runs/latest.md) |\n"
            )
            with patch.object(quality_summary, "HERE", directory):
                self.assertEqual(
                    quality_summary.latest_results(card, {"C1": "accuracy", "C2": "temporal", "C3": "validity"}),
                    {"C1": "PASS", "C2": "FAIL"},
                )

    def test_newer_regression_and_unconfirmed_results(self):
        tags = {"C1": "accuracy", "C2": "temporal"}
        history = "## 歷次結果\n| 2026-01-01 | FAIL | [run](../runs/old.md) |\n"
        self.assertEqual(
            quality_summary.latest_results(history + "| 2026-01-02 | PASS 2／2 | — |\n", tags),
            {"C1": "PASS", "C2": "PASS"},
        )
        self.assertEqual(quality_summary.latest_results(history + "| 2026-01-02 | FAIL | — |\n", tags), {})
        self.assertEqual(quality_summary.latest_results("status: draft\n" + history, tags), {})

    def test_snapshot_question_results(self):
        for number, expected in {
            "031": {"C1": "PASS"},
            "034": {"C1": "PASS", "C2": "PASS", "C3": "PASS"},
            "035": {"C5": "PASS"},
            "039": {"C1": "PASS", "C2": "PASS"},
        }.items():
            path = next(quality_summary.glob.iglob(os.path.join(quality_summary.HERE, "questions", f"Q{number}*.md")))
            with open(path, encoding="utf-8") as file:
                card = file.read()
            tags = dict(quality_summary.re.findall(r"^### (C\d+) ([\w-]+)", card, quality_summary.re.M))
            results = quality_summary.latest_results(card, tags)
            for assertion, result in expected.items():
                self.assertEqual(results[assertion], result)


if __name__ == "__main__":
    unittest.main()
