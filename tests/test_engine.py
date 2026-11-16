"""Engine tests for Race Pace Forge."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import (  # noqa: E402
    build_course,
    build_plan,
    format_duration,
    format_pace,
    heat_index_c,
    normalize_profile,
    parse_brief,
    parse_duration,
    running_energy_cost,
    time_factor,
)

SAMPLES = Path(__file__).resolve().parents[1] / "samples"


def load_sample(name: str) -> dict:
    return build_plan({"brief": (SAMPLES / f"{name}.txt").read_text(encoding="utf-8")})


class DurationTests(unittest.TestCase):
    def test_clock_formats(self):
        self.assertEqual(parse_duration("3:30:00"), 12600)
        self.assertEqual(parse_duration("3:30"), 210)
        self.assertEqual(parse_duration("45:30"), 2730)
        self.assertEqual(parse_duration("3h30m"), 12600)
        self.assertEqual(parse_duration("210 min"), 12600)
        self.assertIsNone(parse_duration("banana"))

    def test_format(self):
        self.assertEqual(format_duration(12600), "3:30:00")
        self.assertEqual(format_duration(2730), "45:30")
        self.assertEqual(format_pace(482, "mi"), "8:02")
        self.assertEqual(format_pace(480, "km"), "8:00")


class BriefTests(unittest.TestCase):
    def test_aliases_and_units(self):
        raw = parse_brief("Distance: half\nGoal time: 1:45:00\nWeight: 165 lb\nTemp: 70F\n")
        profile = normalize_profile(raw)
        self.assertEqual(profile["distance"], "half")
        self.assertEqual(profile["goal_time_s"], 6300)
        self.assertAlmostEqual(profile["weight_kg"], 74.8, places=1)
        self.assertAlmostEqual(profile["temp_c"], 21.1, places=1)

    def test_missing_goal_time_is_an_error(self):
        with self.assertRaises(ValueError):
            normalize_profile({"distance": "5k"})

    def test_course_descriptor_drives_the_profile(self):
        flat = normalize_profile({"distance": "marathon", "goal_time": "3:30:00", "course": "flat"})
        hilly = normalize_profile({"distance": "marathon", "goal_time": "3:30:00", "course": "hilly"})
        flat_course = build_course(flat)
        hilly_course = build_course(hilly)
        self.assertEqual(flat_course["source"], "estimated")
        self.assertGreater(hilly_course["total_gain_m"], flat_course["total_gain_m"] * 5)


class PhysicsTests(unittest.TestCase):
    def test_minetti_cost_is_minimum_on_the_flat(self):
        flat = running_energy_cost(0.0)
        self.assertAlmostEqual(flat, 3.6, places=3)
        self.assertGreater(running_energy_cost(0.05), flat)
        self.assertLess(running_energy_cost(-0.05), flat)
        self.assertLess(running_energy_cost(0.05), running_energy_cost(0.10))

    def test_uphill_is_slower_downhill_is_faster(self):
        self.assertGreater(time_factor(0.05), 1.0)
        self.assertLess(time_factor(-0.05), 1.0)
        self.assertAlmostEqual(time_factor(0.0), 1.0, places=6)

    def test_heat_index(self):
        hot = heat_index_c(33, 55)
        self.assertIn(hot["risk"], ("danger", "extreme"))
        self.assertGreater(hot["heat_index_f"], 95)
        mild = heat_index_c(12, 60)
        self.assertEqual(mild["risk"], "low")


class PlanTests(unittest.TestCase):
    def test_splits_sum_to_the_goal_time(self):
        plan = load_sample("chicago-marathon-330")
        total = sum(split["split_time_s"] for split in plan["pace"]["splits"])
        self.assertAlmostEqual(total, plan["profile"]["goal_time_s"], delta=2.0)

    def test_uphill_splits_are_slower_than_downhill(self):
        plan = load_sample("chicago-marathon-330")
        splits = plan["pace"]["splits"]
        uphill = [s for s in splits if s["grade_pct"] > 0.5]
        downhill = [s for s in splits if s["grade_pct"] < -0.5]
        self.assertTrue(uphill)
        self.assertTrue(downhill)
        avg_up = sum(s["pace_s_per_unit"] for s in uphill) / len(uphill)
        avg_down = sum(s["pace_s_per_unit"] for s in downhill) / len(downhill)
        self.assertGreater(avg_up, avg_down)

    def test_strategies_order_the_halves_correctly(self):
        plan = load_sample("chicago-marathon-330")
        strategies = {s["id"]: s for s in plan["pace"]["strategies"]}

        def half_time(strategy):
            rows = strategy["splits"]
            return rows[len(rows) // 2]["cum_time_s"]

        even_half = half_time(strategies["even"])
        negative_half = half_time(strategies["negative"])
        conservative_half = half_time(strategies["conservative"])
        self.assertGreater(negative_half, even_half)
        self.assertGreater(conservative_half, even_half)
        self.assertLess(negative_half, conservative_half)
        for strategy in strategies.values():
            self.assertAlmostEqual(strategy["splits"][-1]["cum_time_s"],
                                   plan["profile"]["goal_time_s"], delta=2.0)

    def test_zones_run_from_slow_to_fast(self):
        plan = load_sample("chicago-marathon-330")
        zones = plan["fitness"]["zones"]
        self.assertEqual(len(zones), 5)
        # Harder zones are faster, so the pace bands shrink as the zone rises.
        previous_slow = None
        previous_fast = None
        for zone in zones:
            if previous_slow is not None:
                self.assertLess(zone["slow_s_per_unit"], previous_slow)
                self.assertLess(zone["fast_s_per_unit"], previous_fast)
            previous_slow = zone["slow_s_per_unit"]
            previous_fast = zone["fast_s_per_unit"]
        self.assertLess(zones[-1]["fast_s_per_unit"], zones[0]["slow_s_per_unit"])

    def test_fueling_targets_scale_with_duration(self):
        short = load_sample("novice-5k")
        long_race = load_sample("desert-50k-heat")
        self.assertLess(short["fueling"]["carb_target_g_h"], 45)
        self.assertGreaterEqual(long_race["fueling"]["carb_target_g_h"], 75)
        self.assertLessEqual(long_race["fueling"]["carb_target_g_h"], 90)

    def test_a_clean_brief_scores_high(self):
        plan = load_sample("chicago-marathon-330")
        self.assertGreaterEqual(plan["scorecard"]["overall"], 75)
        self.assertIn(plan["scorecard"]["verdict"], ("race_ready", "mostly_ready"))

    def test_an_overambitious_goal_is_capped(self):
        plan = load_sample("first-marathon-305")
        self.assertLessEqual(plan["scorecard"]["overall"], 55)
        ids = {finding["id"] for finding in plan["findings"]}
        self.assertTrue({"goal_faster_than_long_run", "goal_far_faster_than_long_run",
                         "goal_outruns_fitness"} & ids)

    def test_heat_and_injury_are_flagged_and_capped(self):
        plan = load_sample("desert-50k-heat")
        ids = {finding["id"] for finding in plan["findings"]}
        self.assertIn("heat", ids)
        self.assertIn("injury", ids)
        self.assertLessEqual(plan["scorecard"]["overall"], 55)

    def test_a_thin_brief_cannot_be_called_race_ready(self):
        plan = load_sample("novice-5k")
        self.assertLessEqual(plan["scorecard"]["overall"], 50)
        axes = {axis["id"]: axis for axis in plan["scorecard"]["axes"]}
        self.assertIsNone(axes["training"]["score"])

    def test_every_finding_has_evidence_and_a_fix(self):
        for name in ("chicago-marathon-330", "first-marathon-305", "desert-50k-heat",
                     "heat-wave-10k", "injured-return-half", "novice-5k"):
            plan = load_sample(name)
            for finding in plan["findings"]:
                self.assertTrue(finding["evidence"], finding["id"])
                self.assertTrue(finding["why"], finding["id"])
                self.assertTrue(finding["fix"], finding["id"])
                self.assertIn(finding["severity"],
                              ("critical", "high", "medium", "low", "info"))

    def test_priority_actions_are_ordered_by_severity(self):
        plan = load_sample("desert-50k-heat")
        order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        severities = [order[action["severity"]] for action in plan["priority_actions"]]
        self.assertEqual(severities, sorted(severities))

    def test_training_plan_has_cutbacks_and_a_taper(self):
        plan = load_sample("chicago-marathon-330")
        training = plan["training_plan"]
        self.assertTrue(training["available"])
        self.assertTrue(training["cutback_weeks"])
        self.assertTrue(training["taper_weeks"])
        self.assertEqual(training["rows"][-1]["phase"], "taper")


if __name__ == "__main__":
    unittest.main()
