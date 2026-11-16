"""Frontend contract tests - the HTML and JS must agree on ids and fields."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")

REQUIRED_IDS = [
    "brief", "reviewProfile", "analyze", "sampleChips", "error", "empty", "output",
    "overall", "raceTitle", "raceSummary", "verdict", "profileLabel", "axes", "caps",
    "tabs", "tabBody", "save", "rating", "notes", "saveMsg", "expMd", "expJson",
    "expCsv", "runList",
]


class FrontendTests(unittest.TestCase):
    def test_every_required_id_is_in_the_html(self):
        for element_id in REQUIRED_IDS:
            self.assertIn(f'id="{element_id}"', HTML, element_id)

    def test_every_required_id_is_wired_in_the_js(self):
        for element_id in REQUIRED_IDS:
            self.assertIn(f'$("{element_id}")', JS, element_id)

    def test_the_js_reads_the_plan_fields_the_engine_returns(self):
        for field in ("scorecard", "pace", "fitness", "fueling", "training_plan",
                      "findings", "priority_actions", "strengths", "model_notes",
                      "course"):
            self.assertIn(f".{field}", JS, field)

    def test_the_tabs_match_the_render_functions(self):
        tabs = set(re.findall(r'data-tab="([a-z]+)"', HTML))
        functions = set(re.findall(r"function render(Pace|Zones|Fuel|Training|Findings|Fixes|Notes)", JS))
        expected = {"pace", "zones", "fuel", "training", "findings", "fixes", "notes"}
        self.assertEqual(tabs, expected)
        self.assertEqual({name.lower() for name in functions}, expected)

    def test_the_api_routes_the_js_calls_exist_in_the_app(self):
        app_source = (ROOT / "app.py").read_text(encoding="utf-8")
        for route in ("/api/analyze", "/api/runs", "/api/profiles", "/api/samples"):
            self.assertIn(route, JS)
            self.assertIn(route, app_source)


if __name__ == "__main__":
    unittest.main()
