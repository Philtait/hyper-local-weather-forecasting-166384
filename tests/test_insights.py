"""Behavioural checks for the rule-based insights engine."""

import ast
import unittest
from pathlib import Path

import pandas as pd

from insights import generate_insights, load_config, sms_text

ROOT = Path(__file__).resolve().parents[1]
KEYS = ["device_id", "type", "severity", "start_date", "message", "basis", "as_of", "caveat"]
CAVEAT = "Rainfall forecasts are less reliable than temperature forecasts."


def forecast(rain, device="A", start="2026-11-24", upper=None):
    """Seven-day (or shorter) synthetic forecast for one station."""
    days = pd.date_range(start, periods=len(rain), freq="D")
    return pd.DataFrame({
        "device_id": device, "date": days, "rain_mm": rain,
        "rain_upper": upper if upper is not None else rain,
        "temp_min": 16.0, "temp_max": 29.0, "wind_max": 4.0,
    })


def of_type(insights, kind):
    return [item for item in insights if item["type"] == kind]


class InsightsTests(unittest.TestCase):
    def test_every_rule_triggers_with_required_keys(self):
        insights = generate_insights(forecast([0, 0, 2, 25, 55, 0, 0]))
        types = {(item["type"], item["severity"]) for item in insights}
        self.assertEqual(types, {("flood_alert", "watch"), ("flood_alert", "warning"),
                                 ("planting_window", "advisory"), ("rain_delay", "advisory")})
        for item in insights:
            self.assertEqual(list(item), KEYS)
            self.assertEqual(item["as_of"], "2026-11-23")
            self.assertEqual(item["caveat"], CAVEAT)
            self.assertEqual(item["device_id"], "A")

    def test_dry_week_returns_nothing(self):
        self.assertEqual(generate_insights(forecast([0, 0.5, 0, 0.99, 0, 0, 0])), [])

    def test_flood_boundaries(self):
        # Days 4 to 7 hold the boundary values; days 1 to 3 stay dry.
        insights = of_type(generate_insights(forecast([0, 0, 0, 19.99, 20, 49.99, 50])), "flood_alert")
        found = {item["start_date"]: item["severity"] for item in insights}
        self.assertEqual(found, {"2026-11-28": "watch", "2026-11-29": "watch", "2026-11-30": "warning"})

    def test_upper_estimate_reaching_higher_tier_is_mentioned(self):
        insights = of_type(generate_insights(forecast([0, 0, 0, 30, 0, 0, 0],
                                                      upper=[0, 0, 0, 60, 0, 0, 0])), "flood_alert")
        self.assertEqual(len(insights), 1)
        self.assertEqual(insights[0]["severity"], "watch")
        self.assertIn("upper forecast estimate", insights[0]["message"])
        same_tier = of_type(generate_insights(forecast([0, 0, 0, 30, 0, 0, 0],
                                                       upper=[0, 0, 0, 45, 0, 0, 0])), "flood_alert")
        self.assertNotIn("upper forecast estimate", same_tier[0]["message"])

    def test_planting_window_exactly_at_threshold(self):
        insights = of_type(generate_insights(forecast([0, 0, 0, 6.5, 6.5, 7, 0])), "planting_window")
        self.assertEqual(len(insights), 1)
        self.assertEqual(insights[0]["start_date"], "2026-11-25")
        self.assertIn("20.0 mm", insights[0]["message"])
        self.assertIn("Dry-spell risk beyond the 7-day forecast horizon is not assessed",
                      insights[0]["message"])

    def test_planting_window_near_miss_with_two_rain_days(self):
        self.assertEqual(of_type(generate_insights(forecast([0, 0, 0, 10, 10, 0, 0])), "planting_window"), [])
        # 0.5 mm is below the 1 mm rain-day threshold, so only two rain days.
        self.assertEqual(of_type(generate_insights(forecast([0, 0, 0, 10, 9.5, 0.5, 0])), "planting_window"), [])

    def test_rain_delay_includes_day_three_but_not_day_four(self):
        day3 = of_type(generate_insights(forecast([0, 0, 5, 0, 0, 0, 0])), "rain_delay")
        self.assertEqual([item["start_date"] for item in day3], ["2026-11-26"])
        day4 = of_type(generate_insights(forecast([0, 0, 0, 5, 0, 0, 0])), "rain_delay")
        self.assertEqual(day4, [])

    def test_missing_and_non_numeric_values_give_no_insight_and_no_error(self):
        nan = float("nan")
        frame = forecast([nan] * 7, upper=[nan] * 7)
        frame[["temp_min", "temp_max", "wind_max"]] = nan
        self.assertEqual(generate_insights(frame), [])
        frame = forecast(["60", None, True, float("inf"), nan, "x", nan])
        frame["rain_mm"] = frame["rain_mm"].astype(object)
        self.assertEqual(generate_insights(frame), [])

    def test_missing_day_inside_planting_window_does_not_count_as_rain(self):
        frame = forecast([0, 10, float("nan"), 10, 0, 0, 0])
        self.assertEqual(of_type(generate_insights(frame), "planting_window"), [])

    def test_two_stations_are_independent_and_ordered(self):
        wet = forecast([0, 0, 0, 55, 0, 0, 0], device="B")
        dry = forecast([0] * 7, device="A", start="2026-11-20")
        late = forecast([30, 0, 0, 0, 0, 0, 0], device="C", start="2026-12-01")
        frame = pd.concat([late, wet, dry]).sample(frac=1, random_state=3)
        insights = generate_insights(frame)
        self.assertEqual([item["device_id"] for item in insights], ["B", "C", "C"])
        self.assertEqual({item["as_of"] for item in insights if item["device_id"] == "C"}, {"2026-11-30"})
        self.assertEqual(insights[0]["severity"], "warning")
        self.assertEqual(generate_insights(frame), generate_insights(frame.sort_index()))

    def test_sms_text_is_always_under_160_and_keeps_the_action(self):
        insights = generate_insights(forecast([3, 2, 4, 25, 55, 0, 0]))
        self.assertEqual(len({item["type"] for item in insights}), 3)
        for item in insights:
            for area in [None, "", "Busia", "Nambale Township Ward " * 20]:
                text = sms_text(item, area=area)
                self.assertLess(len(text), 160)
                self.assertTrue(text.isascii())
            long_text = sms_text(item, area="X" * 500)
            short_text = sms_text(item, area="")
            self.assertTrue(long_text.endswith(short_text))

    def test_sms_wording_is_conditional_and_never_truncated(self):
        insights = generate_insights(forecast([3, 2, 4, 25, 55, 0, 0]))
        expected = {
            ("flood_alert", "warning"): "Flood warning 28 Nov: very heavy rain expected. "
                                        "If you are in a flood-prone area, move to higher ground.",
            ("flood_alert", "watch"): "Flood watch 27 Nov: heavy rain expected. "
                                      "Clear drains and avoid low-lying fields.",
            ("planting_window", "advisory"): "Rain from 24 Nov may suit planting. "
                                             "Dry spells beyond 7 days are not checked.",
            ("rain_delay", "advisory"): "Rain expected from 24 Nov: delay spraying and "
                                        "harvesting on rainy days.",
        }
        self.assertEqual({(item["type"], item["severity"]) for item in insights}, set(expected))
        long_area = "Nambale Township Ward, Busia County, Western Kenya " * 10
        for item in insights:
            action = expected[(item["type"], item["severity"])]
            self.assertEqual(sms_text(item, area=""), action)
            for area in [None, "Busia", long_area]:
                text = sms_text(item, area=area)
                self.assertLess(len(text), 160)
                self.assertTrue(text.endswith(action))

    def test_every_config_section_has_a_source(self):
        config = load_config()
        for section in ["forecast", "flood_alert", "planting_window", "rain_delay", "sms"]:
            self.assertTrue(config[section]["source"])
        self.assertEqual(config["rain_delay"]["source"], "application-defined, not a published standard")
        self.assertIn("dry spell", config["planting_window"]["limitation"])

    def test_no_numeric_thresholds_are_hard_coded(self):
        tree = ast.parse((ROOT / "insights.py").read_text(encoding="utf-8"))
        numbers = {node.value for node in ast.walk(tree)
                   if isinstance(node, ast.Constant) and type(node.value) in (int, float)}
        self.assertLessEqual(numbers, {0, 1})


if __name__ == "__main__":
    unittest.main()
