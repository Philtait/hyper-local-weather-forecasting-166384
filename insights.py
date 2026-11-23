"""Rule-based farmer advisories from daily 7-day station forecasts.

Input: a DataFrame with one row per station and day and the columns
device_id, date, rain_mm, rain_upper, temp_min, temp_max, wind_max.

Output: a list of insight dicts with the keys device_id, type, severity,
start_date, message, basis, as_of and caveat. Dates are ISO strings.

Rules (thresholds and their sources live in insights_config.json):
  flood_alert      watch / warning when a day's rain_mm reaches the KMD heavy /
                   very heavy rainfall thresholds; one insight per day.
  planting_window  first span of consecutive days with enough total rain and
                   rain days; dry-spell risk beyond the horizon is not assessed.
  rain_delay       rain expected within the next few days: delay field work.

A missing or non-numeric value never triggers a rule and never raises. Each
station is processed independently, in sorted device_id order.
"""

import json
import math
from datetime import timedelta
from numbers import Real
from pathlib import Path

import pandas as pd

CONFIG_PATH = Path(__file__).resolve().parent / "insights_config.json"
REQUIRED_COLUMNS = ["device_id", "date", "rain_mm", "rain_upper", "temp_min", "temp_max", "wind_max"]
RULES = ["forecast", "flood_alert", "planting_window", "rain_delay", "sms"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def load_config(path=CONFIG_PATH):
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    for rule in RULES:
        if not config.get(rule, {}).get("source"):
            raise ValueError(f"insights config: '{rule}' needs a 'source' note")
    if not config.get("caveat"):
        raise ValueError("insights config: 'caveat' is required")
    return config


def as_number(value):
    """Return a finite float, or None for NaN, None, strings, booleans or inf."""
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def iso(day):
    return day.isoformat()


def short_date(day):
    return f"{day.day} {MONTHS[day.month - 1]}"


def mm(value):
    return f"{value:.1f} mm"


def flood_tier(value, rule):
    """Return 'warning', 'watch' or None for a rain amount (None if missing)."""
    if value is None:
        return None
    if value >= rule["warning_mm"]:
        return "warning"
    if value >= rule["watch_mm"]:
        return "watch"
    return None


def station_days(group, horizon_days):
    """Map each calendar day in the horizon to its row values (or None)."""
    dates = group["date"]
    as_of = dates.min() - timedelta(days=1)
    horizon = [as_of + timedelta(days=offset) for offset in range(1, horizon_days + 1)]
    rows = {row["date"]: row for _, row in group.iterrows()}
    days = []
    for day in horizon:
        row = rows.get(day)
        days.append({
            "date": day,
            "rain_mm": as_number(row["rain_mm"]) if row is not None else None,
            "rain_upper": as_number(row["rain_upper"]) if row is not None else None,
        })
    return as_of, days


def flood_alerts(days, rule):
    alerts = []
    for day in days:
        severity = flood_tier(day["rain_mm"], rule)
        if severity is None:
            continue
        label = "Very heavy" if severity == "warning" else "Heavy"
        threshold = rule[f"{severity}_mm"]
        message = (f"Flood {severity}: {label.lower()} rain of {mm(day['rain_mm'])} "
                   f"expected on {iso(day['date'])}.")
        upper_tier = flood_tier(day["rain_upper"], rule)
        if upper_tier == "warning" and severity == "watch":
            message += (f" The upper forecast estimate ({mm(day['rain_upper'])}) reaches "
                        f"the warning level.")
        alerts.append({
            "type": "flood_alert",
            "severity": severity,
            "start_date": day["date"],
            "message": message,
            "basis": f"rain_mm {mm(day['rain_mm'])} >= {threshold} mm ({severity}_mm)",
        })
    return alerts


def planting_window(days, rule, horizon_days):
    span = rule["window_days"]
    for start in range(0, len(days) - span + 1):
        window = days[start:start + span]
        known = [day["rain_mm"] for day in window if day["rain_mm"] is not None]
        total = sum(known)
        rain_days = sum(value >= rule["rain_day_mm"] for value in known)
        if total >= rule["min_total_mm"] and rain_days >= rule["min_rain_days"]:
            first, last = window[0]["date"], window[-1]["date"]
            return [{
                "type": "planting_window",
                "severity": "advisory",
                "start_date": first,
                "message": (f"Planting window: {mm(total)} of rain expected from {iso(first)} to "
                            f"{iso(last)}, with {rain_days} rain days. Dry-spell risk beyond the "
                            f"{horizon_days}-day forecast horizon is not assessed."),
                "basis": (f"{span}-day total {mm(total)} >= {rule['min_total_mm']} mm and "
                          f"{rain_days} days >= {rule['rain_day_mm']} mm "
                          f"(min {rule['min_rain_days']})"),
            }]
    return []


def rain_delay(days, rule, as_of):
    last_day = as_of + timedelta(days=rule["lookahead_days"])
    wet = [day for day in days
           if day["date"] <= last_day
           and day["rain_mm"] is not None and day["rain_mm"] >= rule["rain_day_mm"]]
    if not wet:
        return []
    listed = ", ".join(iso(day["date"]) for day in wet)
    return [{
        "type": "rain_delay",
        "severity": "advisory",
        "start_date": wet[0]["date"],
        "message": (f"Rain expected on {listed}. Delay field work such as spraying or "
                    f"harvesting on those days."),
        "basis": (f"rain_mm >= {rule['rain_day_mm']} mm within {rule['lookahead_days']} days "
                  f"of {iso(as_of)} ({rule['source']})"),
    }]


def prepare(forecasts):
    missing = [column for column in REQUIRED_COLUMNS if column not in forecasts.columns]
    if missing:
        raise ValueError(f"forecasts are missing columns: {missing}")
    frame = forecasts[REQUIRED_COLUMNS].copy()
    frame["device_id"] = frame["device_id"].astype("string").str.strip()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
    # A row without a station or a usable date cannot be placed in the forecast.
    frame = frame.loc[frame["device_id"].notna() & frame["device_id"].ne("") & frame["date"].notna()]
    if frame.duplicated(["device_id", "date"]).any():
        raise ValueError("forecasts contain duplicate device_id/date rows")
    return frame


def generate_insights(forecasts, config=None):
    """Return insight dicts for every station, in sorted device_id order."""
    config = config or load_config()
    horizon = config["forecast"]["horizon_days"]
    insights = []
    frame = prepare(forecasts)
    for device_id in sorted(frame["device_id"].unique()):
        group = frame.loc[frame["device_id"].eq(device_id)]
        as_of, days = station_days(group, horizon)
        found = (flood_alerts(days, config["flood_alert"])
                 + planting_window(days, config["planting_window"], horizon)
                 + rain_delay(days, config["rain_delay"], as_of))
        for item in found:
            item.update(device_id=device_id, start_date=iso(item["start_date"]),
                        as_of=iso(as_of), caveat=config["caveat"])
            insights.append(item)
    return [{key: item[key] for key in
             ["device_id", "type", "severity", "start_date", "message", "basis", "as_of",
              "caveat"]}
            for item in insights]


def sms_action(insight, config):
    """One plain-English action for an insight, built from its own fields."""
    day = short_date(pd.Timestamp(insight["start_date"]).date())
    kind, severity = insight["type"], insight["severity"]
    if kind == "flood_alert" and severity == "warning":
        return f"Flood warning {day}: very heavy rain expected. Move people and livestock to higher ground."
    if kind == "flood_alert":
        return f"Flood watch {day}: heavy rain expected. Clear drains and keep off low-lying fields."
    if kind == "planting_window":
        horizon = config["forecast"]["horizon_days"]
        return f"Good rains from {day}: you can plant. Dry spells after {horizon} days not checked."
    if kind == "rain_delay":
        return f"Rain expected from {day}: delay spraying and harvesting on rainy days."
    raise ValueError(f"unknown insight type: {kind!r}")


def sms_text(insight, area=None, config=None):
    """Plain-English SMS with one action, strictly within the configured limit.

    The area name (default: the device_id) is shortened or dropped to make
    room; the action is never shortened.
    """
    config = config or load_config()
    limit = config["sms"]["max_chars"]
    action = sms_action(insight, config)
    if len(action) > limit:
        raise ValueError(f"SMS action exceeds {limit} characters: {action!r}")
    area = str(area if area is not None else insight["device_id"]).strip()
    room = limit - len(action) - len(": ")
    if not area or room <= 0:
        return action
    if len(area) > room:
        area = area[:room - len("...")].rstrip() + "..." if room > len("...") else ""
    return f"{area}: {action}" if area else action
