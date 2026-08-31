from __future__ import annotations

import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path

from hh_market_monitor import (
    calculate_search_force_model,
    export_csv,
    init_db,
    recalculate_search_force_history,
    save_search_force_snapshot,
    save_snapshot,
)


class RecruitingSupportModelTests(unittest.TestCase):
    def test_reference_example(self) -> None:
        result = calculate_search_force_model(21, 3511, 25)
        self.assertAlmostEqual(result["hr_vacancy_density"], 5.9812, places=4)
        self.assertEqual(result["required_recruiters_base"], 140.44)
        self.assertEqual(result["required_recruiters_min"], 87.78)
        self.assertEqual(result["required_recruiters_max"], 175.55)
        self.assertEqual(result["proxy_support_index_base"], 86.99)
        self.assertEqual(result["proxy_support_index_low"], 80.69)
        self.assertEqual(result["proxy_support_index_high"], 89.32)

    def test_proxy_is_monotonic_in_posted_hr_demand(self) -> None:
        low_demand = calculate_search_force_model(5, 3500, 25)
        high_demand = calculate_search_force_model(50, 3500, 25)
        self.assertGreater(low_demand["proxy_support_index"], high_demand["proxy_support_index"])
        self.assertLess(low_demand["proxy_deficit_index"], high_demand["proxy_deficit_index"])

    def test_invalid_input_returns_unknown(self) -> None:
        result = calculate_search_force_model(None, 3500, 25)
        self.assertIsNone(result["hr_vacancy_density"])
        self.assertIsNone(result["proxy_support_index"])


class StorageAndBackfillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()

    def _save(self, day: int, recruiter_open: int) -> None:
        model = calculate_search_force_model(recruiter_open, 3511, 25)
        row = {
            "query_id": "it_recruiting_power",
            "label": "IT-рекрутеры",
            "query_text": "IT-рекрутер",
            "area": "1",
            "recruiter_vacancy_active_now": recruiter_open,
            "recruiter_vacancy_published_30d": recruiter_open,
            "recruiter_resume_active_now": 100,
            "recruiter_resume_published_30d": 50,
            "recruiter_competition_stock": 4.0,
            "recruiter_competition_flow": 5.0,
            "target_vacancy_active_total": 3511,
            "target_vacancy_published_30d_total": 1000,
            "recruiting_gap_stock": recruiter_open / 3511,
            "recruiting_gap_flow": recruiter_open / 1000,
            "force_error": None,
            **model,
        }
        save_search_force_snapshot(
            self.conn,
            f"2026-01-{day:02d}T00:00:00+00:00",
            row,
        )

    def test_observed_score_requires_five_snapshots(self) -> None:
        for day, value in enumerate([20, 21, 22, 23, 24, 25], start=1):
            self._save(day, value)
        recalculate_search_force_history(self.conn, 25)
        rows = self.conn.execute(
            "SELECT observed_support_score FROM search_force_snapshot ORDER BY collected_at"
        ).fetchall()
        self.assertIsNone(rows[3][0])
        self.assertIsNotNone(rows[4][0])

    def test_csv_column_count_is_stable(self) -> None:
        self._save(1, 21)
        save_snapshot(
            self.conn,
            "2026-01-01T00:00:00+00:00",
            {
                "query_id": "devops",
                "label": "DevOps-инженер",
                "query_text": "DevOps-инженер",
                "area": "1",
                "vacancy_active_now": 100,
                "vacancy_published_30d": 50,
                "resume_active_now": 200,
                "resume_active_published_30d": 100,
                "competition_stock": 2.0,
                "competition_flow": 2.0,
                "resume_error": None,
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "market.csv"
            export_csv(self.conn, output)
            with output.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.reader(handle, delimiter=";"))
            self.assertEqual(len(rows), 2)
            self.assertEqual(len(rows[0]), len(rows[1]))
            self.assertEqual(len(rows[0]), 35)

    def test_migration_adds_v19_columns(self) -> None:
        legacy = sqlite3.connect(":memory:")
        legacy.row_factory = sqlite3.Row
        try:
            legacy.execute(
                """
                CREATE TABLE search_force_snapshot (
                    collected_at TEXT NOT NULL, query_id TEXT NOT NULL,
                    label TEXT NOT NULL, query_text TEXT NOT NULL, area TEXT NOT NULL,
                    recruiter_vacancy_active_now INTEGER,
                    recruiter_vacancy_published_30d INTEGER,
                    target_vacancy_active_total INTEGER,
                    target_vacancy_published_30d_total INTEGER,
                    recruiting_gap_stock REAL, recruiting_gap_flow REAL,
                    search_force_index REAL, force_error TEXT,
                    PRIMARY KEY (collected_at, query_id, area)
                )
                """
            )
            init_db(legacy)
            columns = {row["name"] for row in legacy.execute("PRAGMA table_info(search_force_snapshot)")}
            self.assertTrue(
                {
                    "hr_vacancy_density",
                    "observed_support_score",
                    "proxy_support_index",
                    "proxy_deficit_index",
                    "required_recruiters_base",
                }.issubset(columns)
            )
        finally:
            legacy.close()


if __name__ == "__main__":
    unittest.main()
