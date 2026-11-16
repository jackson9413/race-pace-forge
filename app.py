"""Flask API and static app for Race Pace Forge."""
from __future__ import annotations

import csv
import io
import os
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, request, send_from_directory

from db import (
    DEFAULT_DB,
    delete_run,
    get_run,
    init_db,
    list_runs,
    rate_run,
    save_run,
)
from engine import REVIEW_PROFILES, build_plan, format_duration, format_pace

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
SAMPLES_DIR = BASE_DIR / "samples"


def create_app(test_config: dict[str, Any] | None = None) -> Flask:
    app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")
    app.config.from_mapping(
        DATABASE=os.environ.get("RACEPACE_DB", str(DEFAULT_DB)),
        JSON_SORT_KEYS=False,
        MAX_CONTENT_LENGTH=256 * 1024,
    )
    if test_config:
        app.config.update(test_config)

    init_db(app.config["DATABASE"])

    @app.get("/")
    def index() -> Response:
        return send_from_directory(STATIC_DIR, "index.html")

    @app.get("/api/health")
    def health() -> Response:
        return jsonify({"status": "ok", "service": "race-pace-forge", "version": "1.0.0"})

    @app.get("/api/profiles")
    def profiles() -> Response:
        return jsonify({
            "profiles": [
                {"id": key, "label": value["label"], "weights": value["weights"]}
                for key, value in REVIEW_PROFILES.items()
            ]
        })

    @app.get("/api/samples")
    def samples() -> Response:
        items = []
        for path in sorted(SAMPLES_DIR.glob("*.txt")):
            text = path.read_text(encoding="utf-8")
            title = path.stem.replace("_", " ").title()
            for line in text.splitlines():
                if line.strip().startswith("#"):
                    title = line.strip().lstrip("#").strip() or title
                    break
            items.append({"id": path.stem, "title": title, "text": text})
        return jsonify({"samples": items})

    @app.post("/api/analyze")
    def analyze() -> Response:
        payload = request.get_json(silent=True) or {}
        try:
            plan = build_plan(payload)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"plan": plan})

    @app.get("/api/runs")
    def runs_index() -> Response:
        return jsonify({"runs": list_runs(app.config["DATABASE"])})

    @app.post("/api/runs")
    def runs_create() -> Response:
        payload = request.get_json(silent=True) or {}
        rating = payload.get("rating")
        if rating is not None:
            try:
                rating = int(rating)
            except (TypeError, ValueError):
                return jsonify({"error": "rating must be a number between 1 and 5"}), 400
            if rating < 1 or rating > 5:
                return jsonify({"error": "rating must be between 1 and 5"}), 400
        try:
            plan = build_plan(payload)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        saved = save_run(app.config["DATABASE"], plan, rating, str(payload.get("notes") or ""))
        return jsonify({"run": saved}), 201

    @app.get("/api/runs/<int:run_id>")
    def runs_show(run_id: int) -> Response:
        run = get_run(app.config["DATABASE"], run_id)
        if not run:
            return jsonify({"error": "run not found"}), 404
        return jsonify({"run": run})

    @app.post("/api/runs/<int:run_id>/rating")
    def runs_rate(run_id: int) -> Response:
        payload = request.get_json(silent=True) or {}
        rating = payload.get("rating")
        if rating is not None:
            try:
                rating = int(rating)
            except (TypeError, ValueError):
                return jsonify({"error": "rating must be a number between 1 and 5"}), 400
            if rating < 1 or rating > 5:
                return jsonify({"error": "rating must be between 1 and 5"}), 400
        updated = rate_run(app.config["DATABASE"], run_id, rating, str(payload.get("notes") or ""))
        if not updated:
            return jsonify({"error": "run not found"}), 404
        return jsonify({"run": updated})

    @app.delete("/api/runs/<int:run_id>")
    def runs_delete(run_id: int) -> Response:
        if not delete_run(app.config["DATABASE"], run_id):
            return jsonify({"error": "run not found"}), 404
        return jsonify({"deleted": True, "id": run_id})

    @app.get("/api/runs/<int:run_id>/export")
    def runs_export(run_id: int) -> Response:
        run = get_run(app.config["DATABASE"], run_id)
        if not run:
            return jsonify({"error": "run not found"}), 404
        export_format = request.args.get("format", "md").lower()
        if export_format == "json":
            response = jsonify(run)
            response.headers["Content-Disposition"] = f'attachment; filename="racepace-plan-{run_id}.json"'
            return response
        if export_format == "csv":
            response = Response(splits_csv(run), mimetype="text/csv")
            response.headers["Content-Disposition"] = (
                f'attachment; filename="racepace-splits-{run_id}.csv"')
            return response
        response = Response(plan_to_markdown(run), mimetype="text/markdown")
        response.headers["Content-Disposition"] = f'attachment; filename="racepace-plan-{run_id}.md"'
        return response

    @app.errorhandler(413)
    def too_large(_error: Any) -> Response:
        return jsonify({"error": "request is too large"}), 413

    @app.errorhandler(404)
    def not_found(_error: Any) -> Response:
        if request.path.startswith("/api/"):
            return jsonify({"error": "not found"}), 404
        return send_from_directory(STATIC_DIR, "index.html"), 404

    return app


def splits_csv(plan: dict[str, Any]) -> str:
    unit = plan["pace"]["unit"]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["unit", "split_time", "cumulative_time", "pace", "grade_pct", "gain_m", "clock"])
    for split in plan["pace"]["splits"]:
        writer.writerow([
            split["unit"],
            format_duration(split["split_time_s"]),
            format_duration(split["cum_time_s"]),
            format_pace(split["pace_s_per_unit"], unit),
            split["grade_pct"],
            split["gain_m"],
            split["clock"] or "",
        ])
    return output.getvalue()


def plan_to_markdown(plan: dict[str, Any]) -> str:
    profile = plan["profile"]
    score = plan["scorecard"]
    pace = plan["pace"]
    unit = pace["unit"]
    lines = [
        f"# {profile['race_name']} - {profile['goal_time']} plan",
        "",
        f"**Distance:** {profile['distance']} ({profile['race_km']:.1f} km)  ",
        f"**Goal time:** {profile['goal_time']} (average {pace['avg_pace']} per {unit}, "
        f"flat-equivalent {pace['flat_pace']})  ",
        f"**Race date:** {profile.get('race_date') or 'not given'}  ",
        f"**Start time:** {profile.get('start_time') or 'not given'}  ",
        f"**Course:** {profile['course']} ({plan['course']['source']} profile, "
        f"{plan['course']['total_gain_m']:.0f} m gain)  ",
        f"**Race readiness:** {score['overall']}/100 ({score['verdict_label']})  ",
        f"**Review profile:** {score['profile_label']}",
        "",
        "> " + plan["disclaimer"],
        "",
        "## Scorecard",
        "",
    ]
    for axis in score["axes"]:
        value = "not assessed" if axis["score"] is None else f"{axis['score']}/100"
        lines.append(f"- **{axis['label']}** - {value} (weight {axis['weight']:.0%})")
    if score["caps"]:
        lines.extend(["", "### Score caps applied", ""])
        for cap in score["caps"]:
            limit = "n/a" if cap["limit"] is None else f"{cap['limit']}"
            lines.append(f"- {cap['axis']} (limit {limit}): {cap['reason']}")

    lines.extend(["", "## Pace plan", "", "| Unit | Split | Cumulative | Pace | Grade | Clock |",
                   "|---|---|---|---|---|---|"])
    for split in pace["splits"]:
        lines.append(
            f"| {split['unit']} | {format_duration(split['split_time_s'])} | "
            f"{format_duration(split['cum_time_s'])} | {format_pace(split['pace_s_per_unit'], unit)} | "
            f"{split['grade_pct']:+.1f}% | {split['clock'] or '-'} |")

    lines.extend(["", "### Pacing strategies", ""])
    for strategy in pace["strategies"]:
        checkpoints = ", ".join(
            f"{point['label']} {point['time']}" for point in strategy["checkpoints"])
        lines.append(f"- **{strategy['label']}** - {strategy['note']}")
        lines.append(f"  - Checkpoints: {checkpoints}")

    fitness = plan["fitness"]
    lines.extend(["", "## Training zones", "",
                  f"Estimated VO2max: **{fitness['vo2max']}** (from {fitness['vo2max_source']}). "
                  f"The goal pace works out to about {fitness['goal_fraction']}% of VO2max, "
                  f"which sits in the {fitness['goal_zone']} zone.", "",
                  "| Zone | % VO2max | Fast | Slow | Purpose |", "|---|---|---|---|---|"])
    for zone in fitness["zones"]:
        lines.append(f"| {zone['label']} | {zone['lo_pct']}-{zone['hi_pct']}% | {zone['fast']} | "
                     f"{zone['slow']} | {zone['purpose']} |")

    fueling = plan["fueling"]
    weather = plan["weather"]
    lines.extend(["", "## Fuel and hydration", "",
                  f"- Carbohydrate: **{fueling['carb_target_g_h']} g/h** "
                  f"(range {fueling['carb_range_g_h'][0]}-{fueling['carb_range_g_h'][1]} g/h), "
                  f"{fueling['carb_total_g']} g in total",
                  f"- Fluid: **{fueling['fluid_target_l_h']} L/h** ({fueling['fluid_total_l']} L in total)",
                  f"- Sodium: **{fueling['sodium_mg_h']} mg/h**",
                  f"- Energy: about {fueling['kcal_per_hour']} kcal/h, {fueling['total_kcal']} kcal in total",
                  f"- Sweat rate: {weather['sweat_rate_l_h']} L/h "
                  f"({'measured' if weather['sweat_measured'] else 'estimated'})",
                  f"- Heat index: {weather['heat_index']['heat_index_f'] or 'unknown'} F "
                  f"({weather['heat_index']['risk']}) - {weather['heat_index']['advice']}",
                  "",
                  "| At | Clock | Carbs | Fluid | Sodium | Action |", "|---|---|---|---|---|---|"])
    for point in fueling["checkpoints"]:
        lines.append(f"| {point['unit']} {unit} | {point['clock'] or '-'} | {point['carbs_g']} g | "
                     f"{point['fluid_l']} L | {point['sodium_mg']} mg | {point['action']} |")

    training = plan["training_plan"]
    lines.extend(["", "## Training plan", ""])
    if training["available"]:
        lines.append(f"{training['weeks']} weeks, from {training['baseline_weekly']} "
                     f"{training['unit']} per week now to a peak of {training['peak_weekly']} "
                     f"{training['unit']}. Cutback weeks: "
                     f"{training['cutback_weeks'] or 'none'}. Taper weeks: {training['taper_weeks']}.")
        lines.extend(["", "| Week | Phase | Weekly | Long run | Focus |", "|---|---|---|---|---|"])
        for row in training["rows"]:
            lines.append(f"| {row['week']} | {row['phase']} | {row['weekly']} {training['unit']} | "
                         f"{row['long_run']} {training['unit']} ({row['long_run_pct']}% of distance) | "
                         f"{row['focus']} |")
    else:
        lines.append(training["reason"])

    lines.extend(["", "## Findings", ""])
    if plan["findings"]:
        for finding in plan["findings"]:
            lines.append(f"### [{finding['severity'].upper()}] {finding['title']}")
            lines.append(f"- **Evidence:** {finding['evidence']}")
            lines.append(f"- **Why it matters:** {finding['why']}")
            lines.append(f"- **Fix:** {finding['fix']}")
            lines.append("")
    else:
        lines.append("No findings. Nothing in the brief raised a flag.")

    lines.extend(["", "## Fix plan", ""])
    for index, action in enumerate(plan["priority_actions"], start=1):
        lines.append(f"{index}. **{action['title']}** ({action['severity']}, "
                     f"{action['axis']}, ~{action['estimated_gain']} points) - {action['fix']}")

    if plan["strengths"]:
        lines.extend(["", "## What is already in good shape", ""])
        for note in plan["strengths"]:
            lines.append(f"- {note}")

    lines.extend(["", "## Model notes", ""])
    for note in plan["model_notes"]:
        lines.append(f"- {note}")
    lines.append("")
    return "\n".join(lines)


app = create_app()

if __name__ == "__main__":
    app.run(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "5000")),
        debug=False,
    )
