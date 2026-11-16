"""Race Pace Forge - local-first race pacing, fueling and training planner.

Standard-library only. No API keys, no network calls, no LLM.
Published models used (documented in the README and in the app's Model notes):
  * Minetti et al. (2002) - energy cost of running as a function of gradient.
  * ACSM running equation - VO2 = 0.2*v + 0.9*v*grade + 3.5 (v in m/min).
  * Daniels running formula - VO2max = 0.2*v + 0.8 for a race run near max effort.
"""
from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta
from typing import Any

MILE_M = 1609.344
FT_M = 0.3048
KM_PER_MILE = 1.609344

DISTANCES_KM = {
    "5k": 5.0,
    "10k": 10.0,
    "half": 21.0975,
    "marathon": 42.195,
    "50k": 50.0,
}
DISTANCE_LABELS = {
    "5k": "5K",
    "10k": "10K",
    "half": "Half marathon",
    "marathon": "Marathon",
    "50k": "50K",
}
DISTANCE_ORDER = ["5k", "10k", "half", "marathon", "50k"]

# Fraction of VO2max a race at this distance is run at, when it is a hard effort.
RACE_INTENSITY = {"5k": 1.00, "10k": 1.00, "half": 0.98, "marathon": 0.87, "50k": 0.85}
# Fraction of VO2max a *target* time at this distance typically represents.
GOAL_INTENSITY = {"5k": 0.95, "10k": 0.92, "half": 0.90, "marathon": 0.85, "50k": 0.83}

ZONES = [
    {"id": "recovery", "label": "Recovery", "lo": 0.85, "hi": 0.89,
     "purpose": "Restore and circulate. Full recovery, never hard."},
    {"id": "easy", "label": "Easy", "lo": 0.80, "hi": 0.84,
     "purpose": "Aerobic base. Conversational, nose-breathing."},
    {"id": "marathon", "label": "Marathon pace", "lo": 0.75, "hi": 0.79,
     "purpose": "Goal-pace durability. One long run per week."},
    {"id": "threshold", "label": "Threshold", "lo": 0.90, "hi": 0.94,
     "purpose": "Lactate threshold. 30-60 minutes of hard."},
    {"id": "vo2max", "label": "VO2 max", "lo": 0.95, "hi": 1.00,
     "purpose": "Short sharp efforts. 1-6 repeats."},
]

PLAN_WEEKS_BY_DISTANCE = {"5k": 8, "10k": 10, "half": 14, "marathon": 18, "50k": 20}
RECOMMENDED_WEEKS = {"5k": 8, "10k": 10, "half": 14, "marathon": 18, "50k": 20}

REVIEW_PROFILES = {
    "general": {"label": "General race planning",
                "weights": {"realism": 0.22, "pace": 0.18, "fueling": 0.20,
                            "training": 0.20, "readiness": 0.20}},
    "first-marathon": {"label": "First marathon",
                       "weights": {"realism": 0.25, "pace": 0.17, "fueling": 0.18,
                                   "training": 0.25, "readiness": 0.15}},
    "marathon-goal": {"label": "Marathon target time",
                      "weights": {"realism": 0.28, "pace": 0.17, "fueling": 0.18,
                                  "training": 0.22, "readiness": 0.15}},
    "ultra": {"label": "Ultra / 50K",
              "weights": {"realism": 0.18, "pace": 0.17, "fueling": 0.26,
                          "training": 0.22, "readiness": 0.17}},
    "heat-day": {"label": "Hot weather race",
                 "weights": {"realism": 0.16, "pace": 0.20, "fueling": 0.24,
                             "training": 0.12, "readiness": 0.28}},
    "race-day": {"label": "Race-day execution",
                 "weights": {"realism": 0.16, "pace": 0.26, "fueling": 0.22,
                             "training": 0.12, "readiness": 0.24}},
}

AXIS_LABELS = {
    "realism": "Goal realism",
    "pace": "Pace & course plan",
    "fueling": "Fueling & hydration",
    "training": "Training support",
    "readiness": "Race-day readiness",
}

BRIEF_ALIASES = {
    "race": "race_name", "race name": "race_name", "event": "race_name",
    "distance": "distance", "race distance": "distance", "event distance": "distance",
    "custom km": "custom_km", "custom distance": "custom_km",
    "goal": "goal_time", "goal time": "goal_time", "target time": "goal_time",
    "finish time": "goal_time", "target": "goal_time",
    "date": "race_date", "race date": "race_date",
    "start time": "start_time", "start": "start_time",
    "course": "course", "terrain": "course", "course type": "course",
    "elevation": "elevation_profile", "elevation profile": "elevation_profile",
    "profile": "elevation_profile",
    "aid stations": "aid_stations", "aid station miles": "aid_stations",
    "water miles": "aid_stations", "aid stations miles": "aid_stations",
    "units": "units",
    "weight": "weight_kg", "body weight": "weight_kg", "weight kg": "weight_kg",
    "age": "age",
    "level": "fitness", "fitness": "fitness", "experience": "fitness",
    "recent race": "recent_race", "recent race distance": "recent_race",
    "recent race time": "recent_race_time",
    "weekly miles": "weekly_miles", "mileage": "weekly_miles",
    "weekly volume": "weekly_miles",
    "long run": "long_run", "long run distance": "long_run",
    "long run time": "long_run_time",
    "threshold pace": "threshold_pace",
    "sweat rate": "sweat_rate",
    "injury": "injuries", "injuries": "injuries",
    "temp": "temp_c", "temperature": "temp_c",
    "humidity": "humidity",
    "wind": "wind_kph",
    "plan weeks": "plan_weeks", "weeks": "plan_weeks",
    "notes": "notes",
}


# ------------------------------------------------------------------ helpers

def _num(text: Any) -> float | None:
    if text is None:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", str(text).replace(",", ""))
    return float(match.group(0)) if match else None


def parse_duration(text: Any) -> int | None:
    """Parse '3:30:00', '3:30', '3h30m', '210 min' into seconds."""
    if text is None:
        return None
    raw = str(text).strip().lower().replace("\u2013", "-").replace("\u2019", "'")
    if not raw:
        return None
    clock = re.match(r"^(\d{1,3}):(\d{1,2})(?::(\d{1,2}))?$", raw)
    if clock:
        first = int(clock.group(1))
        second = int(clock.group(2))
        if clock.group(3) is not None:
            return first * 3600 + second * 60 + int(clock.group(3))
        return first * 3600 + second * 60 if first > 59 else first * 60 + second
    words = re.match(
        r"^(?:(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hour|hours)\s*)?"
        r"(?:(\d+(?:\.\d+)?)\s*(?:m|min|mins|minute|minutes)\s*)?"
        r"(?:(\d+(?:\.\d+)?)\s*(?:s|sec|secs|second|seconds))?$", raw)
    if words and any(words.groups()):
        hours = float(words.group(1) or 0)
        mins = float(words.group(2) or 0)
        secs = float(words.group(3) or 0)
        return int(round(hours * 3600 + mins * 60 + secs))
    plain = re.match(r"^(\d+(?:\.\d+)?)\s*(?:min|mins|minutes)?$", raw)
    if plain:
        return int(round(float(plain.group(1)) * 60))
    return None


def format_duration(seconds: int | float) -> str:
    seconds = int(round(seconds))
    if seconds < 0:
        seconds = 0
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_pace(seconds_per_unit: float, unit: str) -> str:
    seconds_per_unit = max(0.0, seconds_per_unit)
    minutes = int(seconds_per_unit // 60)
    secs = int(round(seconds_per_unit - minutes * 60))
    if secs == 60:
        minutes += 1
        secs = 0
    return f"{minutes}:{secs:02d}"


def parse_date(text: Any) -> str | None:
    if text is None:
        return None
    raw = str(text).strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%b %d %Y", "%B %d %Y", "%m/%d/%y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_distance(text: Any) -> float | None:
    """Return a distance in kilometres from free text like '20 miles', '32.2 km'."""
    if text is None:
        return None
    raw = str(text).strip().lower()
    value = _num(raw)
    if value is None:
        return None
    if "km" in raw or "kilometer" in raw:
        return value
    if "mi" in raw or "mile" in raw:
        return value * KM_PER_MILE
    return value * KM_PER_MILE


def parse_elevation_points(text: Any) -> list[tuple[float, float]]:
    """Parse '5/60, 10/40' into [(marker, elevation_ft), ...]."""
    if text is None:
        return []
    raw = str(text).strip()
    if not raw:
        return []
    points: list[tuple[float, float]] = []
    for chunk in re.split(r"[,;\n]+", raw):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "/" in chunk:
            left, _, right = chunk.partition("/")
            marker = _num(left)
            elevation = _num(right)
            if marker is not None and elevation is not None:
                points.append((marker, elevation))
        else:
            elevation = _num(chunk)
            if elevation is not None:
                points.append((0.0, elevation))
    points.sort(key=lambda item: item[0])
    return points


def running_energy_cost(grade: float) -> float:
    """Minetti et al. (2002) energy cost of running (J/kg/m) for a gradient."""
    g = max(-0.45, min(0.45, grade))
    return (155.4 * g ** 5 - 30.4 * g ** 4 - 43.3 * g ** 3
            + 46.3 * g ** 2 + 19.5 * g + 3.6)


def time_factor(grade: float) -> float:
    """Time multiplier for a segment at constant effort on a gradient."""
    return (running_energy_cost(grade) / running_energy_cost(0.0)) ** (1 / 3)


def vo2_cost(speed_m_per_min: float, grade: float = 0.0) -> float:
    """ACSM running equation: gross VO2 (mL/kg/min)."""
    return 0.2 * speed_m_per_min + 0.9 * speed_m_per_min * grade + 3.5


def speed_at_vo2(fraction: float, vo2max: float) -> float:
    """Speed (m/min) that costs a given fraction of VO2max."""
    target = fraction * vo2max
    if target <= 3.5:
        return 0.0
    return (target - 3.5) / 0.2


def seconds_per_unit(speed_m_per_min: float, unit: str) -> float:
    """Convert speed (m/min) into seconds per kilometre or per mile."""
    if speed_m_per_min <= 0:
        return 0.0
    if unit == "km":
        return 60000.0 / speed_m_per_min
    return MILE_M * 60.0 / speed_m_per_min


def unit_length_km(unit: str) -> float:
    return 1.0 if unit == "km" else KM_PER_MILE


# ------------------------------------------------------------- brief parsing

def parse_brief(text: str) -> dict[str, Any]:
    """Parse a plain-text 'Key: value' race brief into raw fields."""
    raw: dict[str, Any] = {}
    for line in str(text).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if not value:
            continue
        canonical = BRIEF_ALIASES.get(key)
        if canonical is None:
            continue
        if canonical == "recent_race":
            parts = re.split(r"\s+(?:in|@|,)\s*", value, flags=re.IGNORECASE)
            raw["recent_race"] = parts[0].strip()
            if len(parts) > 1:
                raw["recent_race_time"] = parts[1].strip()
        elif canonical == "long_run":
            parts = re.split(r"\s+(?:in|@|,)\s*", value, flags=re.IGNORECASE)
            raw["long_run"] = parts[0].strip()
            if len(parts) > 1:
                raw["long_run_time"] = parts[1].strip()
        elif canonical == "weight_kg":
            low = value.lower()
            if "lb" in low or "lbs" in low or "pound" in low:
                pounds = _num(value) or 0.0
                raw["weight_kg"] = round(pounds * 0.45359237, 1)
            else:
                raw["weight_kg"] = _num(value)
        elif canonical == "temp_c":
            low = value.lower()
            if "f" in low and "c" not in low:
                fahrenheit = _num(value) or 0.0
                raw["temp_c"] = round((fahrenheit - 32) * 5 / 9, 1)
            else:
                raw["temp_c"] = _num(value)
        else:
            raw[canonical] = value
    return raw


def _distance_key(text: Any) -> str | None:
    if text is None:
        return None
    raw = str(text).strip().lower()
    if not raw:
        return None
    if raw in DISTANCES_KM:
        return raw
    compact = raw.replace(".", "").replace(",", "")
    if compact in DISTANCES_KM:
        return compact
    if "custom" in raw:
        return "custom"
    if re.match(r"^\d+(\.\d+)?\s*(km|k)$", raw):
        value = _num(raw) or 0.0
        for key, km in DISTANCES_KM.items():
            if abs(km - value) < 0.15:
                return key
    if "half" in raw:
        return "half"
    if "marathon" in raw:
        return "marathon"
    if "ultra" in raw:
        return "50k"
    return None


def normalize_profile(raw: dict[str, Any]) -> dict[str, Any]:
    """Turn raw brief fields into a canonical, unit-normalised profile."""
    raw = raw or {}

    def get(*keys: str) -> Any:
        for key in keys:
            if raw.get(key) not in (None, ""):
                return raw.get(key)
        return None

    distance = _distance_key(get("distance"))
    custom_km = _num(get("custom_km"))
    if distance is None and custom_km:
        distance = "custom"
    if distance is None:
        distance = "marathon"
    if distance == "custom" and not custom_km:
        custom_km = 42.195
        distance = "marathon"

    race_km = DISTANCES_KM.get(distance, custom_km or 42.195)

    goal_time_s = parse_duration(get("goal_time"))
    if goal_time_s is None or goal_time_s <= 0:
        raise ValueError("a goal time is required (formats: 3:30:00, 3:30, 3h30m)")

    units = str(get("units") or "mi").strip().lower()
    units = "km" if units.startswith("k") else "mi"

    elevation_points = parse_elevation_points(get("elevation_profile"))
    if units == "km":
        elevation_points = [(marker * KM_PER_MILE, elevation) for marker, elevation in elevation_points]

    aid_stations: list[float] = []
    raw_aids = get("aid_stations")
    if raw_aids is not None:
        for chunk in re.split(r"[,;\s]+", str(raw_aids)):
            value = _num(chunk)
            if value is None:
                continue
            aid_stations.append(value * KM_PER_MILE if units == "mi" else value)
    aid_stations = sorted({round(value, 3) for value in aid_stations})

    recent_race = _distance_key(get("recent_race"))
    recent_race_time_s = parse_duration(get("recent_race_time"))

    long_run_km = parse_distance(get("long_run"))
    long_run_time_s = parse_duration(get("long_run_time"))
    weekly_km = parse_distance(get("weekly_miles"))
    threshold_s_per_mile = parse_duration(get("threshold_pace"))

    profile = {
        "race_name": str(get("race_name") or f"{DISTANCE_LABELS.get(distance, 'Race')} plan"),
        "distance": distance,
        "race_km": round(race_km, 4),
        "custom_km": custom_km,
        "goal_time_s": goal_time_s,
        "goal_time": format_duration(goal_time_s),
        "race_date": parse_date(get("race_date")),
        "start_time": str(get("start_time") or "").strip() or None,
        "course": str(get("course") or "rolling").strip().lower(),
        "units": units,
        "elevation_points": elevation_points,
        "elevation_source": "profile" if elevation_points else "estimated",
        "aid_stations": aid_stations,
        "weight_kg": _num(get("weight_kg")),
        "age": _num(get("age")),
        "fitness": str(get("fitness") or "unspecified").strip().lower(),
        "recent_race": recent_race,
        "recent_race_time_s": recent_race_time_s,
        "weekly_miles_km": round(weekly_km, 3) if weekly_km else None,
        "long_run_km": round(long_run_km, 3) if long_run_km else None,
        "long_run_time_s": long_run_time_s,
        "threshold_pace_s": threshold_s_per_mile,
        "sweat_rate": _num(get("sweat_rate")),
        "injuries": str(get("injuries") or "").strip(),
        "temp_c": _num(get("temp_c")),
        "humidity": _num(get("humidity")),
        "wind_kph": _num(get("wind_kph")),
        "plan_weeks": int(_num(get("plan_weeks"))) if _num(get("plan_weeks")) else None,
        "profile": str(get("profile") or "general").strip().lower(),
        "notes": str(get("notes") or "").strip()[:600],
    }
    if profile["profile"] not in REVIEW_PROFILES:
        profile["profile"] = "general"
    if threshold_s_per_mile and units == "km":
        profile["threshold_pace_s_per_unit"] = threshold_s_per_mile * KM_PER_MILE
    else:
        profile["threshold_pace_s_per_unit"] = threshold_s_per_mile
    return profile


# ------------------------------------------------------------------- course

def build_course(profile: dict[str, Any]) -> dict[str, Any]:
    """Build per-unit course segments with elevation and gradient."""
    units = profile["units"]
    race_km = profile["race_km"]
    step_km = unit_length_km(units)
    count = max(1, int(round(race_km / step_km)))
    points = profile["elevation_points"]

    elevations: list[float] = []
    if points:
        markers = [marker for marker, _ in points]
        values = [elevation * FT_M for _, elevation in points]
        for index in range(count + 1):
            marker_km = index * step_km
            if marker_km <= markers[0]:
                elevations.append(values[0])
                continue
            if marker_km >= markers[-1]:
                elevations.append(values[-1])
                continue
            for left, right in zip(markers, markers[1:]):
                if left <= marker_km <= right:
                    ratio = (marker_km - left) / (right - left) if right > left else 0.0
                    elevations.append(values[markers.index(left)]
                                     + (values[markers.index(right)] - values[markers.index(left)]) * ratio)
                    break
    else:
        course = profile["course"]
        amplitude_m, wavelength_km = 30.0, 3.2
        if "hill" in course or "mountain" in course or "trail" in course:
            amplitude_m, wavelength_km = 60.0, 1.6
        elif "flat" in course or "level" in course:
            amplitude_m = 0.0
        elevations = [
            amplitude_m * math.sin(2 * math.pi * (index * step_km) / wavelength_km)
            for index in range(count + 1)
        ]
        if "finish hill" in course or "heartbreak" in course or "boston" in course:
            for index in range(count + 1):
                marker_km = index * step_km
                distance_from_end = race_km - marker_km
                if 0 <= distance_from_end <= 2.4:
                    bump = 60.0 * math.sin(math.pi * (1 - distance_from_end / 2.4))
                    elevations[index] += bump

    segments: list[dict[str, Any]] = []
    total_gain = 0.0
    for index in range(count):
        start = elevations[index]
        end = elevations[index + 1]
        rise = end - start
        if rise > 0:
            total_gain += rise
        grade = rise / (step_km * 1000.0) if step_km else 0.0
        segments.append({
            "index": index + 1,
            "start_km": round(index * step_km, 4),
            "end_km": round((index + 1) * step_km, 4),
            "length_km": step_km,
            "elev_start_m": round(start, 2),
            "elev_end_m": round(end, 2),
            "gain_m": round(max(0.0, rise), 2),
            "grade_pct": round(grade * 100, 2),
            "grade": max(-0.45, min(0.45, grade)),
        })
    return {
        "segments": segments,
        "unit": units,
        "unit_length_km": step_km,
        "count": count,
        "total_gain_m": round(total_gain, 1),
        "total_loss_m": round(sum(max(0.0, s["elev_start_m"] - s["elev_end_m"]) for s in segments), 1),
        "source": profile["elevation_source"],
    }


def pace_plan(profile: dict[str, Any], course: dict[str, Any]) -> dict[str, Any]:
    """Grade-adjusted pacing: flat-equivalent pace, per-unit splits, strategies."""
    segments = course["segments"]
    goal_time_s = profile["goal_time_s"]
    factors = [time_factor(segment["grade"]) for segment in segments]
    weighted = sum(segment["length_km"] * factor
                   for segment, factor in zip(segments, factors))
    # Time = distance x pace, so the flat-equivalent pace is goal time / weighted distance.
    flat_pace_s_per_km = goal_time_s / weighted if weighted else 0.0

    splits: list[dict[str, Any]] = []
    cumulative = 0.0
    clock = profile["start_time"]
    for segment, factor in zip(segments, factors):
        segment_time = segment["length_km"] * flat_pace_s_per_km * factor
        pace_s_per_km = flat_pace_s_per_km * factor
        cumulative += segment_time
        unit_pace_s = pace_s_per_km * (course["unit_length_km"] if course["unit"] == "km" else KM_PER_MILE)
        splits.append({
            "unit": segment["index"],
            "distance_km": round(segment["end_km"], 3),
            "split_time_s": round(segment_time, 1),
            "cum_time_s": round(cumulative, 1),
            "pace_s_per_unit": round(unit_pace_s, 1),
            "grade_pct": segment["grade_pct"],
            "gain_m": segment["gain_m"],
            "clock": _add_clock(clock, cumulative),
        })

    flat_pace_s_per_unit = flat_pace_s_per_km * (1.0 if course["unit"] == "km" else KM_PER_MILE)
    avg_pace_s_per_unit = (goal_time_s / profile["race_km"]) * (
        1.0 if course["unit"] == "km" else KM_PER_MILE)

    strategies = _strategies(profile, course, splits, goal_time_s)

    return {
        "splits": splits,
        "flat_pace_s_per_unit": round(flat_pace_s_per_unit, 1),
        "avg_pace_s_per_unit": round(avg_pace_s_per_unit, 1),
        "flat_pace": format_pace(flat_pace_s_per_unit, course["unit"]),
        "avg_pace": format_pace(avg_pace_s_per_unit, course["unit"]),
        "unit": course["unit"],
        "unit_length_km": course["unit_length_km"],
        "strategies": strategies,
        "flat_pace_s_per_km": round(flat_pace_s_per_km, 2),
    }


def _add_clock(start: str | None, seconds: float) -> str | None:
    if not start:
        return None
    match = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*([ap]m)?$", start.strip().lower())
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    suffix = match.group(3)
    if suffix:
        if suffix == "pm" and hour < 12:
            hour += 12
        if suffix == "am" and hour == 12:
            hour = 0
    base = datetime(2024, 1, 1, hour % 24, minute % 60)
    base += timedelta(seconds=int(round(seconds)))
    stamp = base.strftime("%H:%M:%S")
    return stamp


def _strategies(profile: dict[str, Any], course: dict[str, Any],
                splits: list[dict[str, Any]], goal_time_s: int) -> list[dict[str, Any]]:
    """Even, negative-split and conservative-start pacing strategies."""
    half_index = len(splits) // 2
    first_half_time = sum(item["split_time_s"] for item in splits[:half_index]) or 1.0
    second_half_time = sum(item["split_time_s"] for item in splits[half_index:]) or 1.0
    quarter = max(1, len(splits) // 4)

    def build(name: str, label: str, note: str, first_factor: float, second_factor: float) -> dict[str, Any]:
        rows = []
        cumulative = 0.0
        for index, item in enumerate(splits):
            factor = first_factor if index < half_index else second_factor
            cumulative += item["split_time_s"] * factor
            rows.append({
                "unit": item["unit"],
                "cum_time_s": round(cumulative, 1),
                "clock": _add_clock(profile["start_time"], cumulative),
            })
        checkpoints = []
        for label_unit, fraction in (("25%", 0.25), ("50%", 0.50), ("75%", 0.75), ("100%", 1.0)):
            index = min(len(rows) - 1, int(round(fraction * len(rows)))) if fraction < 1 else len(rows) - 1
            checkpoints.append({
                "label": label_unit,
                "clock": rows[index]["clock"],
                "time": format_duration(rows[index]["cum_time_s"]),
            })
        return {
            "id": name,
            "label": label,
            "note": note,
            "checkpoints": checkpoints,
            "splits": rows,
        }

    # Negative split: the second half is 2% faster than the first half.
    negative_first = goal_time_s / 1.98
    # Conservative start: the first half takes 3% longer than half the goal time.
    conservative_first = (goal_time_s / 2) * 1.03
    return [
        build("even", "Even effort",
              "Hold the same effort throughout. The simplest way to bank time on a rolling course.",
              1.0, 1.0),
        build("negative", "Negative split (2% faster second half)",
              "The second half runs 2% faster than the first half, so the finish is quicker than the "
              "plan. Only safe if the goal pace sits below threshold pace.",
              negative_first / first_half_time,
              (goal_time_s - negative_first) / second_half_time),
        build("conservative", "Conservative start (3% slower first half)",
              "The first half takes 3% longer than half the goal time, then you run to the plan. "
              "The right call in heat or on a hilly course.",
              conservative_first / first_half_time,
              (goal_time_s - conservative_first) / second_half_time),
    ]



# ------------------------------------------------------------ fitness model

# Fraction of VO2max that a hard race at this distance actually costs. The gross
# oxygen cost of running (ACSM) overstates the aerobic demand at very short
# distances because those races are part anaerobic, so the cost is discounted.
RACE_COST_FRACTION = {"5k": 1.05, "10k": 1.04, "half": 1.01, "marathon": 0.95, "50k": 0.93}
# Fraction of VO2max a target time at this distance typically represents.
GOAL_COST_FRACTION = {"5k": 0.97, "10k": 0.94, "half": 0.91, "marathon": 0.88, "50k": 0.86}
# Training zones as a fraction of VO2max (higher = harder).
ZONE_FRACTIONS = [
    {"id": "recovery", "label": "Recovery", "lo": 0.72, "hi": 0.78,
     "purpose": "Restore and circulate. Conversational at best."},
    {"id": "easy", "label": "Easy", "lo": 0.78, "hi": 0.84,
     "purpose": "Aerobic base. You could talk in full sentences."},
    {"id": "marathon", "label": "Marathon pace", "lo": 0.84, "hi": 0.90,
     "purpose": "Goal-pace durability. One long run a week at this pace."},
    {"id": "threshold", "label": "Threshold", "lo": 0.90, "hi": 0.95,
     "purpose": "Lactate threshold. 30-60 minutes of hard."},
    {"id": "vo2max", "label": "VO2 max", "lo": 0.95, "hi": 1.00,
     "purpose": "Short sharp efforts. 1-6 repeats with full recovery."},
]


def _speed_m_per_min(distance_km: float, seconds: int) -> float:
    """Speed in metres per minute for a distance in km and a time in seconds."""
    if not seconds or seconds <= 0:
        return 0.0
    return distance_km * 1000.0 / (seconds / 60.0)


def fitness_model(profile: dict[str, Any]) -> dict[str, Any]:
    """Estimate VO2max from the best evidence available and map training zones."""
    unit = profile["units"]
    goal_speed = _speed_m_per_min(profile["race_km"], profile["goal_time_s"])
    goal_cost = vo2_cost(goal_speed)

    evidence: list[dict[str, Any]] = []
    if profile.get("recent_race") and profile.get("recent_race_time_s"):
        dist_km = DISTANCES_KM.get(profile["recent_race"], 0.0)
        speed = _speed_m_per_min(dist_km, profile["recent_race_time_s"])
        fraction = RACE_COST_FRACTION.get(profile["recent_race"], 1.0)
        evidence.append({
            "source": f"recent {DISTANCE_LABELS.get(profile['recent_race'], 'race')}",
            "detail": format_duration(profile["recent_race_time_s"]),
            "speed_km_per_min": round(speed, 1),
            "vo2max": round(vo2_cost(speed) / fraction, 1),
        })
    if profile.get("threshold_pace_s_per_unit"):
        seconds = profile["threshold_pace_s_per_unit"]
        speed = (MILE_M if unit == "mi" else 1000.0) / (seconds / 60.0)
        evidence.append({
            "source": "threshold pace",
            "detail": format_pace(seconds, unit),
            "speed_km_per_min": round(speed, 1),
            "vo2max": round(vo2_cost(speed) / 0.92, 1),
        })
    if profile.get("long_run_km") and profile.get("long_run_time_s"):
        speed = _speed_m_per_min(profile["long_run_km"], profile["long_run_time_s"])
        evidence.append({
            "source": "long run pace",
            "detail": format_duration(profile["long_run_time_s"]),
            "speed_km_per_min": round(speed, 1),
            "vo2max": round(vo2_cost(speed) / 0.78, 1),
        })

    goal_fraction = GOAL_COST_FRACTION.get(profile["distance"], 0.9)
    goal_vo2max = round(goal_cost / goal_fraction, 1)

    if evidence:
        # Evidence is ordered by reliability: a recent race, then threshold pace, then long run.
        best = evidence[0]
        vo2max = best["vo2max"]
        source = best["source"]
        assessed = True
    else:
        vo2max = goal_vo2max
        source = "goal time only"
        assessed = False

    zones = []
    for zone in ZONE_FRACTIONS:
        fast_speed = speed_at_vo2(zone["hi"], vo2max)
        slow_speed = speed_at_vo2(zone["lo"], vo2max)
        if fast_speed <= 0 or slow_speed <= 0:
            continue
        fast = seconds_per_unit(fast_speed, unit)
        slow = seconds_per_unit(slow_speed, unit)
        zones.append({
            "id": zone["id"],
            "label": zone["label"],
            "purpose": zone["purpose"],
            "lo_pct": round(zone["lo"] * 100),
            "hi_pct": round(zone["hi"] * 100),
            "fast": format_pace(fast, unit),
            "slow": format_pace(slow, unit),
            "fast_s_per_unit": round(fast, 1),
            "slow_s_per_unit": round(slow, 1),
        })

    goal_pace_fraction = goal_cost / vo2max if vo2max else 0.0
    goal_zone = None
    for zone in zones:
        if zone["lo_pct"] / 100 <= goal_pace_fraction <= zone["hi_pct"] / 100:
            goal_zone = zone["label"]
            break
    if goal_zone is None:
        goal_zone = "above every training zone" if goal_pace_fraction > 1 else "below every training zone"

    return {
        "vo2max": round(vo2max, 1),
        "vo2max_source": source,
        "assessed": assessed,
        "evidence": evidence,
        "goal_vo2max": goal_vo2max,
        "goal_fraction": round(goal_pace_fraction * 100, 1),
        "goal_zone": goal_zone,
        "zones": zones,
        "goal_cost": round(goal_cost, 1),
        "goal_speed_m_per_min": round(goal_speed, 1),
        "unit": unit,
    }


# ------------------------------------------------------------- weather model

def heat_index_c(temp_c: float | None, humidity: float | None) -> dict[str, Any]:
    """Rothfusz heat index in Celsius (None when inputs are missing)."""
    if temp_c is None or humidity is None:
        return {"heat_index_c": None, "heat_index_f": None, "risk": "unknown", "advice": ""}
    temp_f = temp_c * 9 / 5 + 32
    rh = max(0.0, min(100.0, humidity))
    if temp_f >= 80 and rh >= 40:
        hi_f = (-42.379 + 2.04901523 * temp_f + 10.14333127 * rh
                - 0.22475541 * temp_f * rh - 0.00683783 * temp_f ** 2
                - 0.05481717 * rh ** 2 + 0.00122874 * temp_f ** 2 * rh
                + 0.00085282 * temp_f * rh ** 2
                - 0.00000199 * temp_f ** 2 * rh ** 2)
    else:
        hi_f = temp_f + 0.55 * (1 - 0.01 * rh) * (temp_f - 78)
    hi_c = (hi_f - 32) * 5 / 9
    if hi_f < 80:
        risk, advice = "low", "Comfortable. No heat adjustment needed."
    elif hi_f < 90:
        risk, advice = "caution", "Watch your effort early. Sip on a schedule rather than when thirsty."
    elif hi_f < 100:
        risk, advice = "danger", "Run 1-3% slower than goal pace, add fluid, and use cooling at every aid station."
    else:
        risk, advice = "extreme", "Heat emergency. Reconsider the goal pace, add 30-40% more fluid, cool at every station."
    return {
        "heat_index_c": round(hi_c, 1),
        "heat_index_f": round(hi_f, 1),
        "risk": risk,
        "advice": advice,
    }


def weather_model(profile: dict[str, Any]) -> dict[str, Any]:
    temp_c = profile.get("temp_c")
    humidity = profile.get("humidity")
    wind = profile.get("wind_kph")
    heat = heat_index_c(temp_c, humidity)
    sweat_rate = profile.get("sweat_rate")
    sweat_measured = bool(sweat_rate)
    if not sweat_rate:
        sweat_rate = 0.5
        if temp_c is not None:
            sweat_rate += 0.02 * max(0.0, temp_c - 15)
        if humidity is not None and humidity > 50:
            sweat_rate *= 1 + 0.004 * (humidity - 50)
        sweat_rate = round(min(1.6, max(0.3, sweat_rate)), 2)
    target_fluid = round(sweat_rate * 0.75, 2)
    cap = 1.0 if (heat["heat_index_f"] or 0) >= 90 else 0.8
    target_fluid = min(target_fluid, cap)
    target_fluid = max(0.4, target_fluid)
    sodium = int(300 + (sweat_rate - 0.5) * 400)
    if (heat["heat_index_f"] or 0) >= 85:
        sodium += 150
    return {
        "temp_c": temp_c,
        "humidity": humidity,
        "wind_kph": wind,
        "sweat_rate_l_h": sweat_rate,
        "sweat_measured": sweat_measured,
        "fluid_target_l_h": target_fluid,
        "fluid_cap_l_h": cap,
        "sodium_mg_h": max(200, min(800, sodium)),
        "heat_index": heat,
        "cold": bool(temp_c is not None and temp_c <= 5),
        "windy": bool(wind is not None and wind >= 25),
    }


# -------------------------------------------------------------- fueling plan

FUEL_ITEMS = [
    {"name": "Energy gel", "carbs_g": 25, "note": "1 gel = 25 g carbohydrate"},
    {"name": "Chews", "carbs_g": 30, "note": "30 g carbohydrate, easy to chew while running"},
    {"name": "Sports drink (500 ml)", "carbs_g": 25, "note": "500 ml at 5% carbohydrate"},
    {"name": "Energy bar", "carbs_g": 30, "note": "30 g carbohydrate - eat early, it needs water"},
    {"name": "Rice ball", "carbs_g": 40, "note": "40 g carbohydrate, low fat, high salt"},
    {"name": "Boiled potatoes + salt", "carbs_g": 30, "note": "30 g carbohydrate, easy to digest"},
]


def fueling_model(profile: dict[str, Any], weather: dict[str, Any],
                  course: dict[str, Any]) -> dict[str, Any]:
    duration_h = profile["goal_time_s"] / 3600.0
    if duration_h < 1.5:
        carb_lo, carb_hi, carb_target = 30, 45, 38
    elif duration_h < 2.5:
        carb_lo, carb_hi, carb_target = 45, 60, 52
    elif duration_h < 4.0:
        carb_lo, carb_hi, carb_target = 60, 75, 68
    else:
        carb_lo, carb_hi, carb_target = 75, 90, 82

    weight_kg = profile.get("weight_kg") or 70.0
    speed = fitness_model(profile)["goal_speed_m_per_min"]
    grade = 0.0
    if course["segments"]:
        grade = sum(s["grade"] for s in course["segments"]) / len(course["segments"])
    vo2 = vo2_cost(speed, grade)
    kcal_per_min = vo2 * weight_kg * 5 / 1000
    total_kcal = kcal_per_min * (profile["goal_time_s"] / 60.0)

    fluid_target = weather["fluid_target_l_h"]
    checkpoints = _fuel_checkpoints(profile, course, carb_target, fluid_target, weather)
    carb_total = round(carb_target * duration_h)
    fluid_total = round(fluid_target * duration_h, 1)

    per_hour_items = []
    for item in FUEL_ITEMS:
        per_hour_items.append({
            "name": item["name"],
            "carbs_g": item["carbs_g"],
            "per_hour": round(carb_target / item["carbs_g"], 1),
            "note": item["note"],
        })

    pre_carbs = round(weight_kg * 11)
    return {
        "duration_h": round(duration_h, 2),
        "carb_target_g_h": carb_target,
        "carb_range_g_h": [carb_lo, carb_hi],
        "carb_total_g": carb_total,
        "carb_ceiling_g_h": 90,
        "fluid_target_l_h": fluid_target,
        "fluid_total_l": fluid_total,
        "sodium_mg_h": weather["sodium_mg_h"],
        "kcal_per_min": round(kcal_per_min, 1),
        "kcal_per_hour": round(kcal_per_min * 60),
        "total_kcal": round(total_kcal),
        "weight_kg": weight_kg,
        "checkpoints": checkpoints,
        "per_hour_items": per_hour_items,
        "pre_race": {
            "carbs_24h_g": pre_carbs,
            "carbs_1_4h_g": int(weight_kg * 2),
            "fluid_2_4h_l": round(weight_kg * 0.007, 1),
            "caffeine_mg": 200,
            "note": "Load 10-12 g carbohydrate per kg the day before, top up 1-4 g/kg in the "
                    "last few hours, and take 200 mg caffeine about 45 minutes before the start.",
        },
    }


def _fuel_checkpoints(profile: dict[str, Any], course: dict[str, Any], carb_target: int,
                      fluid_target: float, weather: dict[str, Any]) -> list[dict[str, Any]]:
    """Fuel checkpoints at aid stations, or every 30 minutes when none are known."""
    duration_s = profile["goal_time_s"]
    unit_km = course["unit_length_km"]
    unit = course["unit"]
    pace_s_per_unit = duration_s / max(1, course["count"])
    stations = profile["aid_stations"]
    rows: list[dict[str, Any]] = []
    if stations:
        for index, station_km in enumerate(stations):
            marker = station_km / course["unit_length_km"]
            minutes = max(0.0, marker * pace_s_per_unit / 60.0)
            rows.append({
                "unit": round(marker, 1),
                "minutes": round(minutes),
                "clock": _add_clock(profile["start_time"], minutes * 60),
                "carbs_g": round(carb_target * minutes / 60.0),
                "fluid_l": round(fluid_target * minutes / 60.0, 2),
                "sodium_mg": int(weather["sodium_mg_h"] * minutes / 60.0),
                "action": f"Water + {round(carb_target / 25)} gel equivalent, 250 ml minimum",
            })
    else:
        elapsed = 0.0
        while elapsed < duration_s:
            minutes = elapsed / 60.0
            rows.append({
                "unit": round(elapsed / pace_s_per_unit, 1),
                "minutes": round(minutes),
                "clock": _add_clock(profile["start_time"], elapsed),
                "carbs_g": round(carb_target * minutes / 60.0),
                "fluid_l": round(fluid_target * minutes / 60.0, 2),
                "sodium_mg": int(weather["sodium_mg_h"] * minutes / 60.0),
                "action": f"{round(carb_target / 25)} gel equivalent + 250 ml water",
            })
            elapsed += 1800
    if rows:
        last = rows[-1]
        last["action"] = last["action"] + " (finish strong)"
    return rows


# ------------------------------------------------------------ training plan

def training_plan(profile: dict[str, Any]) -> dict[str, Any]:
    """Build a periodised plan around the current weekly volume."""
    distance = profile["distance"]
    recommended = RECOMMENDED_WEEKS.get(distance, 14)
    weeks = profile.get("plan_weeks") or recommended
    weeks = max(4, min(52, weeks))
    baseline_km = profile.get("weekly_miles_km")
    long_run_km = profile.get("long_run_km")
    race_km = profile["race_km"]
    unit = profile["units"]
    to_unit = 1.0 if unit == "km" else 1.0 / KM_PER_MILE

    if not baseline_km and not long_run_km:
        return {
            "available": False,
            "reason": "No weekly mileage or long-run distance was given, so no plan could be built.",
            "weeks": weeks,
            "weeks_recommended": recommended,
            "rows": [],
        }

    baseline = baseline_km or (long_run_km or 0.0) * 1.6
    peak_factor = {"5k": 1.2, "10k": 1.25, "half": 1.35, "marathon": 1.45, "50k": 1.55}.get(distance, 1.35)
    peak_weekly = baseline * peak_factor
    long_baseline = long_run_km or min(race_km * 0.6, 32.0)
    peak_long = min(long_baseline * peak_factor, race_km * 1.15)

    rows = []
    for week in range(1, weeks + 1):
        to_race = weeks - week + 1
        if to_race <= 3:
            taper_multiplier = {3: 0.75, 2: 0.60, 1: 0.40}[to_race]
            phase = "taper"
            weekly = peak_weekly * taper_multiplier
            long_run = peak_long * taper_multiplier
            focus = "Taper: keep the legs fresh, keep the rhythm"
            sessions = [
                f"Easy {round(weekly * 0.25 * to_unit, 1)} {unit}",
                "Strides 6 x 100 m, full recovery",
                f"Easy {round(weekly * 0.3 * to_unit, 1)} {unit}",
                f"Long run {round(long_run * to_unit, 1)} {unit} with goal-pace strides",
            ]
        elif week % 4 == 0:
            phase = "cutback"
            growth = min(1.08 ** (week - 1), peak_factor)
            weekly = baseline * growth * 0.75
            long_run = min(long_baseline * growth * 0.75, race_km * 1.15)
            focus = "Cutback week: absorb the last block of work"
            sessions = [
                f"Easy {round(weekly * 0.3 * to_unit, 1)} {unit}",
                "Strides only, no hard session",
                f"Easy {round(weekly * 0.35 * to_unit, 1)} {unit}",
                f"Long run {round(long_run * to_unit, 1)} {unit} easy",
            ]
        elif week <= max(4, weeks // 3):
            phase = "base"
            growth = min(1.08 ** (week - 1), peak_factor)
            weekly = baseline * growth
            long_run = min(long_baseline * growth, race_km * 1.15)
            focus = "Base: easy volume, one long run"
            sessions = [
                f"Easy {round(weekly * 0.3 * to_unit, 1)} {unit}",
                f"Easy {round(weekly * 0.3 * to_unit, 1)} {unit} with strides",
                f"Recovery {round(weekly * 0.2 * to_unit, 1)} {unit}",
                f"Long run {round(long_run * to_unit, 1)} {unit}",
            ]
        else:
            phase = "build"
            growth = min(1.08 ** (week - 1), peak_factor)
            weekly = baseline * growth
            long_run = min(long_baseline * growth, race_km * 1.15)
            focus = "Build: race-specific work"
            sessions = [
                f"Easy {round(weekly * 0.25 * to_unit, 1)} {unit}",
                "Threshold: 6 x 2 mi at threshold pace with 90 s jog"
                if distance in ("marathon", "50k") else
                f"Race pace: 6 x 1 mi at goal pace with 2 min jog",
                f"Easy {round(weekly * 0.3 * to_unit, 1)} {unit}",
                f"Long run {round(long_run * to_unit, 1)} {unit}",
            ]
        rows.append({
            "week": week,
            "phase": phase,
            "weekly": round(weekly * to_unit, 1),
            "long_run": round(long_run * to_unit, 1),
            "long_run_pct": round(long_run / race_km * 100, 1),
            "focus": focus,
            "sessions": sessions,
        })

    return {
        "available": True,
        "weeks": weeks,
        "weeks_recommended": recommended,
        "baseline_weekly": round(baseline * to_unit, 1),
        "peak_weekly": round(peak_weekly * to_unit, 1),
        "peak_long_run": round(peak_long * to_unit, 1),
        "unit": unit,
        "rows": rows,
        "cutback_weeks": [row["week"] for row in rows if row["phase"] == "cutback"],
        "taper_weeks": [row["week"] for row in rows if row["phase"] == "taper"],
    }


# ------------------------------------------------------------------ findings

SEVERITY_PENALTY = {"critical": 26, "high": 15, "medium": 8, "low": 3, "info": 0}
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def _finding(finding_id: str, axis: str, severity: str, title: str,
             evidence: str, why: str, fix: str) -> dict[str, Any]:
    return {
        "id": finding_id,
        "axis": axis,
        "severity": severity,
        "title": title,
        "evidence": evidence,
        "why": why,
        "fix": fix,
        "penalty": SEVERITY_PENALTY.get(severity, 0),
    }


def collect_findings(profile: dict[str, Any], course: dict[str, Any], pace: dict[str, Any],
                     fitness: dict[str, Any], weather: dict[str, Any],
                     fueling: dict[str, Any], plan: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    unit = profile["units"]
    goal_time_s = profile["goal_time_s"]
    duration_h = goal_time_s / 3600.0
    goal_pace_s_km = goal_time_s / profile["race_km"]
    long_run_pace_s_km = None
    if profile.get("long_run_km") and profile.get("long_run_time_s"):
        long_run_pace_s_km = profile["long_run_time_s"] / profile["long_run_km"]

    # ---- goal realism
    if long_run_pace_s_km:
        faster = (long_run_pace_s_km - goal_pace_s_km) / long_run_pace_s_km * 100
        if faster > 8:
            findings.append(_finding(
                "goal_far_faster_than_long_run", "realism", "critical",
                "Goal pace is faster than your recent long run by more than 8%",
                f"Goal pace {format_pace(goal_pace_s_km * (1 if unit == 'km' else KM_PER_MILE), unit)} "
                f"vs long-run pace {format_pace(long_run_pace_s_km * (1 if unit == 'km' else KM_PER_MILE), unit)} "
                f"({faster:.1f}% faster).",
                "Marathon pace is normally 3-6% slower than a hard long run. A bigger gap than that "
                "usually means the goal is set from a training pace rather than from race fitness.",
                "Move the goal to a time you can hold for a full long run, then re-cut the plan."))
        elif faster > 4:
            findings.append(_finding(
                "goal_faster_than_long_run", "realism", "high",
                "Goal pace is faster than your recent long run",
                f"Goal pace is {faster:.1f}% faster than the pace of your last long run.",
                "The last long run is your best evidence of what you can hold. Anything much faster "
                "than that pace breaks down in the second half.",
                "Re-run the plan with a goal time 3-5% slower than the long-run pace, or extend "
                "the long run to the race distance before chasing the goal."))
    if fitness["evidence"] and fitness["goal_vo2max"] > fitness["vo2max"] * 1.06:
        findings.append(_finding(
            "goal_outruns_fitness", "realism", "critical",
            "Goal time assumes more fitness than your recent racing shows",
            f"Goal time implies a VO2max of about {fitness['goal_vo2max']}, but your "
            f"{fitness['vo2max_source']} suggests about {fitness['vo2max']}.",
            "When the two disagree by more than 6% the goal is built on a fitness you have not "
            "demonstrated. This is the single most common reason a goal time falls apart.",
            "Set the goal from your evidence (recent race or threshold pace), then move it in "
            "30-second steps once the block is complete."))
    elif fitness["evidence"] and fitness["goal_vo2max"] < fitness["vo2max"] * 0.82:
        findings.append(_finding(
            "goal_conservative", "realism", "info",
            "Goal time is well inside your current fitness",
            f"Goal time implies a VO2max of about {fitness['goal_vo2max']} against evidence of "
            f"about {fitness['vo2max']}.",
            "There is headroom here. Conservative goals get banked, ambitious ones get blown.",
            "Consider moving the goal in 1-2 minute steps if the long run at goal pace feels "
            "conversational."))
    if fitness["goal_fraction"] >= 96 and profile["distance"] in ("marathon", "half", "50k"):
        findings.append(_finding(
            "goal_at_or_above_threshold", "realism", "high",
            "Goal pace sits at or above threshold pace for a long race",
            f"The goal pace works out to about {fitness['goal_fraction']}% of estimated VO2max.",
            "A marathon or longer race cannot be run at threshold effort. The plan has to be "
            "built around an aerobic effort with fuel on board.",
            "Add 2-4% to the goal time and rebuild the splits, or shorten the distance."))
    if not fitness["assessed"]:
        findings.append(_finding(
            "no_fitness_evidence", "realism", "low",
            "Goal realism was judged from the goal time alone",
            "No recent race, threshold pace or long-run time was given.",
            "Without a performance anchor every pace in this plan is an assumption, including "
            "whether the goal is reachable.",
            "Add a recent race time (10K or half) and the plan will be re-scored against real evidence."))
    if profile.get("race_date"):
        try:
            days = (date.fromisoformat(profile["race_date"]) - date.today()).days
        except ValueError:
            days = None
        if days is not None and 0 <= days < 56:
            findings.append(_finding(
                "race_close", "realism", "medium",
                "The race is less than eight weeks away",
                f"The race date is {profile['race_date']}, about {days} days out.",
                "Eight weeks is the minimum for a marathon build. Inside that window you cannot "
                "add much fitness, only protect the fitness you have.",
                "Shift to the plan's current block, keep the volume honest and treat the race as "
                "a training exercise if the goal is out of reach."))

    # ---- pace and course
    if course["source"] == "estimated":
        findings.append(_finding(
            "course_estimated", "pace", "low",
            "Course shape was estimated, not measured",
            "No elevation profile was supplied, so a representative profile was assumed.",
            "Splits are only as good as the terrain. A hilly course changes the pacing maths "
            "by minutes, not seconds.",
            "Add an elevation profile as 'marker/elevation feet' pairs to rebuild the splits."))
    steep = [s for s in course["segments"] if abs(s["grade_pct"]) > 8]
    if steep:
        worst = max(steep, key=lambda s: abs(s["grade_pct"]))
        findings.append(_finding(
            "steep_grade", "pace", "medium",
            "The course contains a very steep section",
            f"Steepest section is {worst['grade_pct']:+.1f}% at {worst['unit']} "
            f"({worst['start_km']:.1f}-{worst['end_km']:.1f} km).",
            "Above about 8% the cost of running climbs steeply and form breaks down on the "
            "descent. Effort control matters more than pace here.",
            "Shorten your stride and quicken your cadence uphill; on the descent take short steps "
            "and let the pace come to you."))
    if course["total_gain_m"] > 0 and course["count"] >= 6:
        third = max(1, course["count"] // 3)
        late_gain = sum(s["gain_m"] for s in course["segments"][-third:])
        if late_gain > course["total_gain_m"] * 0.32:
            findings.append(_finding(
                "late_climb", "pace", "medium",
                "Most of the climbing is in the final third",
                f"{late_gain / max(1.0, course['total_gain_m']) * 100:.0f}% of the total climb "
                f"({late_gain:.0f} m) is in the last third of the course.",
                "Late climbing meets a depleted engine. Fuel and pacing have to be built for the "
                "back half, not the front.",
                "Take carbs earlier than feels necessary and hold back on the descents so you have "
                "something left for the climb."))
    if duration_h > 1.2 and not profile["aid_stations"]:
        findings.append(_finding(
            "no_aid_stations", "pace", "medium",
            "No aid-station information was supplied",
            "The brief does not say where water and fuel are available.",
            "Without aid stations you cannot plan where to take fuel, and long gaps on hot courses "
            "are where races are lost.",
            "Add the aid-station distances and the checkpoints will be placed on your actual course."))
    if len(profile["aid_stations"]) >= 2:
        gaps = [b - a for a, b in zip(profile["aid_stations"], profile["aid_stations"][1:])]
        worst_gap = max(gaps)
        if worst_gap > 4.8 * KM_PER_MILE:
            findings.append(_finding(
                "aid_gap", "pace", "medium",
                "There is a long stretch without an aid station",
                f"The widest gap between stations is {worst_gap / KM_PER_MILE:.1f} miles.",
                "A gap like that means carrying your own fluid or accepting a long time without "
                "sipping, which costs more on a hot day than most runners expect.",
                "Carry a flask for that section, or plan to take fluid at the stations on either side."))
    if not profile.get("start_time"):
        findings.append(_finding(
            "no_start_time", "pace", "low",
            "No start time, so no clock times",
            "The brief has no start time.",
            "Clock times are how you pace a race - by clock, not by effort. Without a start time "
            "the split table has no times to run to.",
            "Add the start time and every split gets a clock time."))

    # ---- fueling and hydration
    if duration_h >= 2.5 and fueling["carb_target_g_h"] < 60:
        findings.append(_finding(
            "carb_rate_low", "fueling", "high",
            "Carbohydrate target is below what a race this long needs",
            f"Goal time {profile['goal_time']} implies a target of "
            f"{fueling['carb_target_g_h']} g per hour.",
            "Past two and a half hours you need roughly 60-90 g of carbohydrate an hour. Under-fuel "
            "is the most common cause of a late-race collapse.",
            "Use a mix of glucose and fructose sources (gel plus drink) so you can absorb the full "
            "amount, and test it on long runs first."))
    if duration_h > 4:
        findings.append(_finding(
            "very_long_fuel", "fueling", "medium",
            "A race over four hours pushes the carbohydrate ceiling",
            f"Target {fueling['carb_target_g_h']} g per hour for {duration_h:.1f} hours "
            f"({fueling['carb_total_g']} g in total).",
            "Absorption tops out around 90 g per hour for a single transportable carbohydrate, so "
            "ultra-distance pacing depends on training the gut.",
            "Mix glucose and fructose 1:0.8, use real food from about two hours in, and rehearse "
            "the exact plan in training."))
    if weather["sweat_measured"]:
        pass
    else:
        findings.append(_finding(
            "sweat_rate_estimated", "fueling", "low",
            "Sweat rate was estimated from the weather, not measured",
            f"Estimated sweat rate {weather['sweat_rate_l_h']} L per hour from temperature and humidity.",
            "Two runners in the same air can sweat at very different rates, and the fluid target "
            "moves with it.",
            "Weigh yourself before and after a long run in similar conditions to get your real number."))
    if weather["fluid_target_l_h"] >= 0.85:
        findings.append(_finding(
            "fluid_target_high", "fueling", "medium",
            "Fluid target is close to the gut's limit",
            f"Target {weather['fluid_target_l_h']} L per hour.",
            "You can only absorb so much at a time. Drinking more than you can tolerate causes "
            "sloshing and, in the worst case, hyponatremia.",
            "Split the volume across more checkpoints and stick to familiar fluids."))
    if duration_h >= 2.0 and weather["sodium_mg_h"] < 450 and (weather["heat_index"]["heat_index_f"] or 0) >= 85:
        findings.append(_finding(
            "sodium_low_in_heat", "fueling", "medium",
            "Sodium replacement is low for a hot race",
            f"{weather['sodium_mg_h']} mg per hour with a heat index of "
            f"{weather['heat_index']['heat_index_f']:.0f} F.",
            "Heavy salt losses show up as cramping and collapse late in the race, and heat makes "
            "them worse.",
            "Add a salty drink or electrolyte tabs to the plan and check the sodium content of "
            "your gels."))
    if weather["sweat_rate_l_h"] - weather["fluid_target_l_h"] > 0.25:
        findings.append(_finding(
            "hydration_deficit", "fueling", "high",
            "You will finish the race dehydrated on these numbers",
            f"Sweat {weather['sweat_rate_l_h']} L per hour against a fluid target of "
            f"{weather['fluid_target_l_h']} L per hour.",
            "A deficit of more than a quarter litre an hour compounds across three hours and shows "
            "up as a slow, heavy finish.",
            "Add fluid at every aid station, sip on a timer rather than when thirsty, and consider "
            "carrying a flask if the stations are sparse."))
    if duration_h >= 2.0:
        findings.append(_finding(
            "practice_fueling", "fueling", "low",
            "The fueling plan needs to be rehearsed in training",
            f"Race time {profile['goal_time']} at {fueling['carb_target_g_h']} g of carbohydrate "
            "per hour.",
            "The gut has to be trained into a rate it is not used to. Nobody gets the target rate "
            "right on race day without practice.",
            "Run one long session at the target carbohydrate rate two to three weeks out."))

    # ---- training support
    if not plan.get("available"):
        findings.append(_finding(
            "no_training_data", "training", "medium",
            "No training data, so the training axis could not be assessed",
            "Neither weekly mileage nor a long run was given.",
            "Race fitness is built in training. Without mileage there is no way to tell whether "
            "the goal is supported by the last two months.",
            "Add weekly miles and the long run from a typical week and the plan will be built around them."))
    else:
        if plan["weeks"] < plan["weeks_recommended"]:
            findings.append(_finding(
                "plan_short", "training", "medium",
                "The plan is shorter than this distance needs",
                f"{plan['weeks']} weeks planned against {plan['weeks_recommended']} recommended "
                f"for a {DISTANCE_LABELS.get(profile['distance'], 'race')}.",
                "Cutting the block short means the race arrives before the fitness does.",
                "Extend the plan, or move the race date and use the extra weeks."))
        if plan["available"] and plan["peak_weekly"] > plan["baseline_weekly"] * 1.32:
            findings.append(_finding(
                "volume_jump", "training", "medium",
                "Peak volume is a big jump from where you are now",
                f"Peak {plan['peak_weekly']} {plan['unit']} per week against a current "
                f"{plan['baseline_weekly']} {plan['unit']}.",
                "Volume that is more than a third higher than the current base is usually injury "
                "waiting to happen, especially with a time goal attached.",
                "Add weeks, not miles: extend the plan so the climb happens over four extra weeks."))
        minimum_weekly = {"marathon": 20 * KM_PER_MILE, "50k": 24 * KM_PER_MILE,
                           "half": 12 * KM_PER_MILE, "10k": 8 * KM_PER_MILE}.get(profile["distance"])
        if minimum_weekly and plan["baseline_weekly"] * (KM_PER_MILE if plan["unit"] == "km" else 1) < minimum_weekly:
            findings.append(_finding(
                "volume_low", "training", "medium",
                "Current mileage is below the usual floor for this distance",
                f"{plan['baseline_weekly']} {plan['unit']} per week now.",
                "Most runners need a base of roughly this much weekly distance before a goal time "
                "at this distance is realistic.",
                "Build the base first and set the time goal for the next block."))
        if profile.get("long_run_km") and profile["long_run_km"] < profile["race_km"] * 0.7:
            findings.append(_finding(
                "long_run_short", "training", "medium",
                "Your longest recent run is well short of the race distance",
                f"Longest run {profile['long_run_km'] / KM_PER_MILE:.1f} miles against a "
                f"{profile['race_km'] / KM_PER_MILE:.1f} mile race.",
                "The long run is where you rehearse fueling, gear and the late-race effort. "
                "Shortening it is the first thing to fix.",
                "Extend the long run gradually toward the race distance over the next three weeks."))
        if profile.get("long_run_km") and profile["long_run_km"] > profile["race_km"] * 1.35:
            findings.append(_finding(
                "long_run_long", "training", "low",
                "The long run is much longer than the race distance",
                f"Longest run {profile['long_run_km'] / KM_PER_MILE:.1f} miles against a "
                f"{profile['race_km'] / KM_PER_MILE:.1f} mile race.",
                "Overshooting is fine, but only if the extra distance is easy. Long runs should "
                "be slower than goal pace.",
                "Keep the extra miles easy, or trim back if the long runs are leaving you sore."))

    # ---- race-day readiness
    if profile.get("injuries"):
        recent = bool(re.search(r"\b(week|day|ago|recent|this)\b", profile["injuries"].lower()))
        findings.append(_finding(
            "injury", "readiness", "critical" if recent else "high",
            "An injury is listed on the brief",
            f"Injuries: {profile['injuries']}.",
            "A race takes the injury history into account whether or not the entry has healed. "
            "Racing an unresolved niggle is how a six-week setback happens.",
            "Get the injury assessed before the start, and if it is still there, defer the race."))
    heat_f = weather["heat_index"]["heat_index_f"]
    if heat_f is not None and heat_f >= 90:
        findings.append(_finding(
            "heat", "readiness", "high",
            "Race-day heat index is in the danger band",
            f"Heat index {heat_f:.0f} F ({weather['heat_index']['heat_index_c']:.0f} C), "
            f"{weather['heat_index']['risk']}.",
            "Above 90 F the heat index runs above air temperature and dehydration risk climbs "
            "sharply. Pacing that works at 60 F will fall apart.",
            "Start 1-3% slower than goal pace, add fluid, and use ice or cold water at every station."))
    if weather["cold"]:
        findings.append(_finding(
            "cold_start", "readiness", "medium",
            "Cold start conditions",
            f"Start temperature {weather['temp_c']:.0f} C.",
            "Cold muscles are injury-prone and slow to wake up. Most early-race injuries happen "
            "in the first two miles.",
            "Start further back than usual, spend the first mile warming up, and wear layers you "
            "can discard."))
    if weather["windy"]:
        findings.append(_finding(
            "wind", "readiness", "low",
            "Strong wind forecast",
            f"Wind {weather['wind_kph']:.0f} kph.",
            "A headwind costs roughly the same as a climb. An out-and-back course with wind on "
            "the return needs a different effort plan.",
            "Budget extra time for the exposed section and take your effort cues from effort, not pace."))

    return findings


# ---------------------------------------------------------------- scorecard

def scorecard(profile: dict[str, Any], findings: list[dict[str, Any]],
              plan: dict[str, Any], fitness: dict[str, Any]) -> dict[str, Any]:
    axes = ["realism", "pace", "fueling", "training", "readiness"]
    scores: dict[str, Any] = {}
    for axis in axes:
        penalty = sum(f["penalty"] for f in findings if f["axis"] == axis)
        scores[axis] = max(0, 100 - penalty)
    if not plan.get("available"):
        scores["training"] = None

    weights = dict(REVIEW_PROFILES[profile["profile"]]["weights"])
    if scores["training"] is None:
        weights["training"] = 0.0
    total_weight = sum(weights.values()) or 1.0
    weights = {axis: round(value / total_weight, 4) for axis, value in weights.items()}

    overall = sum(scores[axis] * weights[axis] for axis in axes if scores[axis] is not None)
    overall = round(overall, 1)

    caps: list[dict[str, Any]] = []

    def apply_cap(axis: str | None, limit: float, overall_limit: float | None, reason: str) -> None:
        nonlocal overall
        if axis and scores[axis] is not None and scores[axis] > limit:
            scores[axis] = limit
            caps.append({"axis": axis, "limit": limit, "reason": reason})
        if overall_limit is not None and overall > overall_limit:
            overall = overall_limit
            caps.append({"axis": "overall", "limit": overall_limit, "reason": reason})

    realism_critical = any(f["id"] in ("goal_far_faster_than_long_run", "goal_outruns_fitness")
                            and f["severity"] == "critical" for f in findings)
    if realism_critical:
        apply_cap("realism", 25, 40, "A critical goal finding caps goal realism and the overall score.")
    elif any(f["id"] == "goal_faster_than_long_run" and f["severity"] == "high" for f in findings):
        apply_cap("realism", 45, 60, "The goal outruns your long-run evidence, so the score is capped.")
    if any(f["id"] in ("carb_rate_low", "hydration_deficit") for f in findings):
        apply_cap("fueling", 45, 70, "A fueling gap caps the fueling axis and the overall score.")
    if any(f["id"] == "heat" for f in findings):
        apply_cap("readiness", 50, 68, "Heat in the danger band caps race-day readiness.")
    if any(f["id"] == "injury" for f in findings):
        apply_cap("readiness", 40, 45, "An unresolved injury caps race-day readiness.")
    if not plan.get("available"):
        caps.append({"axis": "training", "limit": None,
                     "reason": "Training was not assessed, so its weight was spread across the other axes."})
    if any(f["id"] == "no_fitness_evidence" for f in findings):
        apply_cap("realism", 60, None, "Goal realism was judged without any performance evidence.")

    not_assessed = [axis for axis in axes if scores[axis] is None]
    if len(not_assessed) >= 2:
        apply_cap(None, 0, 55, "Two or more axes could not be assessed, so the score is provisional.")
    elif len(not_assessed) == 1:
        apply_cap(None, 0, 70, "One axis could not be assessed, so the score is provisional.")
    if not fitness["assessed"] and not plan.get("available"):
        apply_cap("realism", 35, 45, "The brief has neither a performance result nor training data, "
                                     "so it cannot be judged race ready.") 

    if overall >= 85:
        verdict = "race_ready"
        verdict_label = "Race ready"
    elif overall >= 70:
        verdict = "mostly_ready"
        verdict_label = "Mostly ready"
    elif overall >= 55:
        verdict = "needs_work"
        verdict_label = "Needs work"
    elif overall >= 40:
        verdict = "tight_but_usable"
        verdict_label = "Tight but usable"
    else:
        verdict = "not_ready"
        verdict_label = "Not ready"

    return {
        "axes": [
            {
                "id": axis,
                "label": AXIS_LABELS[axis],
                "score": scores[axis],
                "weight": weights[axis],
                "note": "not assessed" if scores[axis] is None else "",
            }
            for axis in axes
        ],
        "overall": round(overall, 1),
        "verdict": verdict,
        "verdict_label": verdict_label,
        "caps": caps,
        "profile": profile["profile"],
        "profile_label": REVIEW_PROFILES[profile["profile"]]["label"],
    }


def fix_plan(findings: list[dict[str, Any]], score: dict[str, Any]) -> list[dict[str, Any]]:
    weights = {axis["id"]: axis["weight"] for axis in score["axes"]}
    ordered = sorted(findings, key=lambda f: (SEVERITY_ORDER.get(f["severity"], 9), -f["penalty"]))
    plan = []
    for finding in ordered:
        if finding["penalty"] == 0:
            continue
        gain = round(finding["penalty"] * weights.get(finding["axis"], 0.0), 1)
        plan.append({
            "id": finding["id"],
            "title": finding["title"],
            "severity": finding["severity"],
            "axis": AXIS_LABELS.get(finding["axis"], finding["axis"]),
            "fix": finding["fix"],
            "estimated_gain": gain,
        })
    return plan


def strengths(profile: dict[str, Any], fitness: dict[str, Any],
              fueling: dict[str, Any], plan: dict[str, Any]) -> list[str]:
    notes = []
    if fitness["assessed"]:
        notes.append(f"Fitness evidence in hand ({fitness['vo2max_source']}), so the paces are anchored "
                     f"to a real number (VO2max about {fitness['vo2max']}).")
    if profile.get("elevation_points"):
        notes.append("A real elevation profile was supplied, so the splits account for the terrain.")
    if profile.get("aid_stations"):
        notes.append(f"{len(profile['aid_stations'])} aid stations were mapped, so fuel and fluid are "
                     "placed on the actual course.")
    if profile.get("race_date"):
        notes.append("The race date is known, so the training block can be laid out backwards from it.")
    if plan.get("available") and plan["weeks"] >= plan["weeks_recommended"]:
        notes.append(f"The {plan['weeks']}-week block matches the recommended length for the distance.")
    if profile.get("sweat_rate"):
        notes.append("Your sweat rate is measured rather than estimated, so the fluid target is real.")
    if not notes:
        notes.append("The brief is complete enough to build a plan; fill in the gaps below to sharpen it.")
    return notes


def build_plan(payload: dict[str, Any]) -> dict[str, Any]:
    """Top-level entry point: brief text or structured fields in, full plan out."""
    payload = payload or {}
    raw: dict[str, Any] = {}
    if isinstance(payload.get("profile"), dict):
        raw.update(payload["profile"])
    brief = payload.get("brief")
    if brief:
        parsed = parse_brief(brief)
        parsed.update({key: value for key, value in raw.items() if value not in (None, "")})
        raw = parsed
    review = payload.get("review_profile") or raw.pop("profile", None)
    if review:
        raw["profile"] = review

    profile = normalize_profile(raw)
    course = build_course(profile)
    pace = pace_plan(profile, course)
    fitness = fitness_model(profile)
    weather = weather_model(profile)
    fueling = fueling_model(profile, weather, course)
    plan = training_plan(profile)
    findings = collect_findings(profile, course, pace, fitness, weather, fueling, plan)
    score = scorecard(profile, findings, plan, fitness)
    fixes = fix_plan(findings, score)

    return {
        "profile": profile,
        "course": {
            "unit": course["unit"],
            "count": course["count"],
            "total_gain_m": course["total_gain_m"],
            "total_loss_m": course["total_loss_m"],
            "source": course["source"],
            "profile_points": profile["elevation_points"],
            "aid_stations": profile["aid_stations"],
        },
        "pace": pace,
        "fitness": fitness,
        "weather": weather,
        "fueling": fueling,
        "training_plan": plan,
        "findings": findings,
        "scorecard": score,
        "priority_actions": fixes,
        "strengths": strengths(profile, fitness, fueling, plan),
        "model_notes": [
            "Pace splits use the Minetti et al. (2002) energy cost of running on gradients, so uphill "
            "splits are slower and downhill splits faster at the same effort.",
            "VO2max is estimated with the Daniels running formula from your best available evidence "
            "(recent race, threshold pace or long run), and training zones are mapped from the ACSM "
            "running equation. Treat the numbers as a good estimate, not a laboratory result.",
            "Fueling targets follow the standard endurance ranges: 30-45 g of carbohydrate per hour "
            "under 90 minutes, 45-60 g up to 2.5 hours, 60-75 g up to 4 hours, 75-90 g beyond that.",
            "Fluid is capped at the rate your gut can absorb, and sodium is scaled to your estimated "
            "sweat rate and the heat index.",
            "This is a planning tool, not medical or coaching advice. Talk to a clinician about "
            "injuries, and to a coach about anything race-specific.",
        ],
        "disclaimer": "Planning aid only - not medical advice and not a substitute for a coach.",
    }
