"""API tests for Race Pace Forge."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app  # noqa: E402

BRIEF = """Race: Test Marathon
Distance: marathon
Goal time: 3:30:00
Start time: 07:30
Course: flat
Weekly miles: 40
Long run: 20 miles in 1:40:00
Recent race: 10k in 44:00
"""


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.app = create_app({"DATABASE": str(Path(self.tempdir.name) / "test.db"),
                                "TESTING": True})
        self.client = self.app.test_client()

    def tearDown(self):
        self.tempdir.cleanup()

    def test_health(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "ok")

    def test_index_serves_the_app(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Race Pace Forge", response.data)

    def test_profiles_are_exposed(self):
        response = self.client.get("/api/profiles")
        self.assertEqual(response.status_code, 200)
        ids = {item["id"] for item in response.get_json()["profiles"]}
        self.assertIn("general", ids)
        self.assertIn("marathon-goal", ids)

    def test_samples_are_exposed(self):
        response = self.client.get("/api/samples")
        self.assertEqual(response.status_code, 200)
        samples = response.get_json()["samples"]
        self.assertGreaterEqual(len(samples), 6)
        self.assertTrue(all(item["text"].strip() for item in samples))

    def test_analyze_returns_a_full_plan(self):
        response = self.client.post("/api/analyze", json={"brief": BRIEF})
        self.assertEqual(response.status_code, 200)
        plan = response.get_json()["plan"]
        for key in ("profile", "course", "pace", "fitness", "weather", "fueling",
                    "training_plan", "findings", "scorecard", "priority_actions",
                    "strengths", "model_notes"):
            self.assertIn(key, plan)
        self.assertEqual(plan["profile"]["goal_time"], "3:30:00")
        self.assertGreater(len(plan["pace"]["splits"]), 20)

    def test_analyze_rejects_a_brief_without_a_goal_time(self):
        response = self.client.post("/api/analyze", json={"brief": "Distance: marathon"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("goal time", response.get_json()["error"])

    def test_review_profile_changes_the_weighting(self):
        general = self.client.post("/api/analyze", json={
            "brief": BRIEF, "review_profile": "general"}).get_json()["plan"]
        marathon = self.client.post("/api/analyze", json={
            "brief": BRIEF, "review_profile": "marathon-goal"}).get_json()["plan"]
        general_weights = {a["id"]: a["weight"] for a in general["scorecard"]["axes"]}
        marathon_weights = {a["id"]: a["weight"] for a in marathon["scorecard"]["axes"]}
        self.assertNotEqual(general_weights["realism"], marathon_weights["realism"])

    def test_run_lifecycle(self):
        created = self.client.post("/api/runs", json={"brief": BRIEF, "rating": 4,
                                                      "notes": "felt good"})
        self.assertEqual(created.status_code, 201)
        run_id = created.get_json()["run"]["id"]
        listing = self.client.get("/api/runs").get_json()["runs"]
        self.assertEqual(len(listing), 1)
        self.assertEqual(listing[0]["rating"], 4)
        fetched = self.client.get(f"/api/runs/{run_id}")
        self.assertEqual(fetched.status_code, 200)
        rated = self.client.post(f"/api/runs/{run_id}/rating",
                                 json={"rating": 2, "notes": "too fast"})
        self.assertEqual(rated.status_code, 200)
        self.assertEqual(rated.get_json()["run"]["rating"], 2)
        deleted = self.client.delete(f"/api/runs/{run_id}")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(self.client.get(f"/api/runs/{run_id}").status_code, 404)

    def test_rating_is_validated(self):
        response = self.client.post("/api/runs", json={"brief": BRIEF, "rating": 9})
        self.assertEqual(response.status_code, 400)

    def test_exports(self):
        run_id = self.client.post("/api/runs", json={"brief": BRIEF}).get_json()["run"]["id"]
        markdown = self.client.get(f"/api/runs/{run_id}/export?format=md")
        self.assertEqual(markdown.status_code, 200)
        self.assertIn(b"# ", markdown.data)
        self.assertIn(b"Splits", markdown.data)
        csv_export = self.client.get(f"/api/runs/{run_id}/export?format=csv")
        self.assertEqual(csv_export.status_code, 200)
        self.assertIn(b"pace", csv_export.data)
        json_export = self.client.get(f"/api/runs/{run_id}/export?format=json")
        self.assertEqual(json_export.status_code, 200)
        self.assertIn("scorecard", json_export.get_json())

    def test_unknown_api_route_returns_json(self):
        response = self.client.get("/api/nope")
        self.assertEqual(response.status_code, 404)
        self.assertIn("error", response.get_json())


if __name__ == "__main__":
    unittest.main()
