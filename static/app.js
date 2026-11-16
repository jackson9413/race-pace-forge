"use strict";

const $ = (id) => document.getElementById(id);
let currentPlan = null;
let currentTab = "pace";
let lastRunId = null;

function esc(value) {
  return String(value === null || value === undefined ? "" : value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function fmtDur(seconds) {
  seconds = Math.max(0, Math.round(Number(seconds) || 0));
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h) return h + ":" + String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
  return m + ":" + String(s).padStart(2, "0");
}

function fmtPace(seconds) {
  seconds = Math.max(0, Math.round(Number(seconds) || 0));
  const m = Math.floor(seconds / 60);
  let s = seconds % 60;
  if (s === 60) return (m + 1) + ":00";
  return m + ":" + String(s).padStart(2, "0");
}

function verdictClass(verdict) {
  if (verdict === "race_ready" || verdict === "mostly_ready") return "";
  if (verdict === "needs_work") return "needs_work";
  if (verdict === "tight_but_usable") return "tight";
  return "not_ready";
}

async function api(path, options) {
  const response = await fetch(path, options);
  let payload = null;
  try { payload = await response.json(); } catch (err) { payload = null; }
  if (!response.ok) {
    throw new Error((payload && payload.error) || ("request failed: " + response.status));
  }
  return payload;
}

async function init() {
  try {
    const data = await api("/api/profiles");
    const select = $("reviewProfile");
    select.innerHTML = "";
    data.profiles.forEach((profile) => {
      const option = document.createElement("option");
      option.value = profile.id;
      option.textContent = profile.label;
      select.appendChild(option);
    });
    select.value = "general";
  } catch (err) { /* profiles are optional */ }

  try {
    const data = await api("/api/samples");
    const chips = $("sampleChips");
    chips.innerHTML = "";
    data.samples.forEach((sample) => {
      const button = document.createElement("button");
      button.textContent = sample.title;
      button.title = sample.id;
      button.addEventListener("click", () => {
        $("brief").value = sample.text;
        $("error").hidden = true;
      });
      chips.appendChild(button);
    });
  } catch (err) { /* samples are optional */ }

  $("analyze").addEventListener("click", analyze);
  $("save").addEventListener("click", savePlan);
  $("tabs").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-tab]");
    if (!button) return;
    currentTab = button.dataset.tab;
    Array.from($("tabs").children).forEach((child) => child.classList.remove("active"));
    button.classList.add("active");
    renderTab();
  });
  loadRuns();
}

async function analyze() {
  const brief = $("brief").value.trim();
  $("error").hidden = true;
  if (!brief) {
    showError("Paste a race brief first. The goal time is the one thing the planner cannot do without.");
    return;
  }
  $("analyze").disabled = true;
  $("analyze").textContent = "Building...";
  try {
    const data = await api("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ brief: brief, review_profile: $("reviewProfile").value })
    });
    currentPlan = data.plan;
    lastRunId = null;
    $("expMd").removeAttribute("href");
    $("expJson").removeAttribute("href");
    $("expCsv").removeAttribute("href");
    $("saveMsg").textContent = "";
    render();
  } catch (err) {
    showError(err.message);
  } finally {
    $("analyze").disabled = false;
    $("analyze").textContent = "Build plan";
  }
}

function showError(message) {
  const box = $("error");
  box.textContent = message;
  box.hidden = false;
}

function render() {
  const plan = currentPlan;
  $("empty").hidden = true;
  $("output").hidden = false;

  const score = plan.scorecard;
  const profile = plan.profile;
  const pace = plan.pace;
  $("overall").textContent = score.overall;
  $("raceTitle").textContent = profile.race_name + " - " + profile.goal_time;
  const parts = [
    profile.distance,
    "average " + pace.avg_pace + "/" + pace.unit,
    "flat-equivalent " + pace.flat_pace + "/" + pace.unit,
    plan.course.total_gain_m + " m of climbing",
    "VO2max ~" + plan.fitness.vo2max
  ];
  if (profile.race_date) parts.push("race " + profile.race_date);
  $("raceSummary").textContent = parts.join("  |  ");
  const verdict = $("verdict");
  verdict.textContent = score.verdict_label;
  verdict.className = "verdict " + verdictClass(score.verdict);
  $("profileLabel").textContent = score.profile_label;

  $("axes").innerHTML = score.axes.map((axis) => {
    if (axis.score === null) {
      return '<div class="axis na"><div class="name"><span>' + esc(axis.label) +
        '</span><span>weight ' + Math.round(axis.weight * 100) + '%</span></div>' +
        '<div class="val">not assessed</div></div>';
    }
    const color = axis.score >= 80 ? "var(--accent)" : axis.score >= 55 ? "var(--warn)" : "var(--bad)";
    return '<div class="axis"><div class="name"><span>' + esc(axis.label) +
      '</span><span>weight ' + Math.round(axis.weight * 100) + '%</span></div>' +
      '<div class="val">' + axis.score + '</div>' +
      '<div class="bar"><i style="width:' + axis.score + "%;background:" + color + '"></i></div></div>';
  }).join("");

  $("caps").innerHTML = (score.caps || []).map((cap) =>
    '<div><b>' + esc(cap.axis) + '</b> (limit ' + (cap.limit === null ? "n/a" : cap.limit) +
    '): ' + esc(cap.reason) + '</div>').join("");

  renderTab();
}

function renderTab() {
  if (!currentPlan) return;
  const body = $("tabBody");
  const plan = currentPlan;
  if (currentTab === "pace") body.innerHTML = renderPace(plan);
  else if (currentTab === "zones") body.innerHTML = renderZones(plan);
  else if (currentTab === "fuel") body.innerHTML = renderFuel(plan);
  else if (currentTab === "training") body.innerHTML = renderTraining(plan);
  else if (currentTab === "findings") body.innerHTML = renderFindings(plan);
  else if (currentTab === "fixes") body.innerHTML = renderFixes(plan);
  else body.innerHTML = renderNotes(plan);
}

function renderPace(plan) {
  const pace = plan.pace;
  const rows = pace.splits.map((split) =>
    "<tr><td>" + split.unit + "</td><td class=\"num\">" + fmtDur(split.split_time_s) +
    "</td><td class=\"num\">" + fmtDur(split.cum_time_s) +
    "</td><td class=\"num\">" + fmtPace(split.pace_s_per_unit) +
    "</td><td class=\"num\">" + (split.grade_pct > 0 ? "+" : "") + split.grade_pct.toFixed(1) + "%</td>" +
    "<td class=\"num\">" + (split.clock || "-") + "</td></tr>").join("");
  const strategies = pace.strategies.map((strategy) => {
    const points = strategy.checkpoints.map((point) =>
      "<tr><td>" + esc(point.label) + "</td><td class=\"num\">" + esc(point.time) +
      "</td><td class=\"num\">" + esc(point.clock || "-") + "</td></tr>").join("");
    return '<h3>' + esc(strategy.label) + '</h3><p class="hint">' + esc(strategy.note) + '</p>' +
      '<div class="tr"><table><thead><tr><th>Checkpoint</th><th class="num">Elapsed</th>' +
      '<th class="num">Clock</th></tr></thead><tbody>' + points + "</tbody></table></div>";
  }).join("");
  return "<h3>Splits (" + pace.unit + ")</h3>" +
    '<p class="hint">Grade-adjusted at constant effort: uphill splits are slower, downhill splits faster. ' +
    "Clock times start at your start time.</p>" +
    '<div class="tr"><table><thead><tr><th>Unit</th><th class="num">Split</th>' +
    '<th class="num">Cumulative</th><th class="num">Pace</th><th class="num">Grade</th>' +
    "<th class=\"num\">Clock</th></tr></thead><tbody>" + rows + "</tbody></table></div>" +
    "<h3>Pacing strategies</h3>" + strategies;
}

function renderZones(plan) {
  const fitness = plan.fitness;
  const evidence = fitness.evidence.length
    ? '<div class="tr"><table><thead><tr><th>Evidence</th><th class="num">Result</th>' +
      '<th class="num">Speed</th><th class="num">VO2max</th></tr></thead><tbody>' +
      fitness.evidence.map((item) =>
        "<tr><td>" + esc(item.source) + "</td><td class=\"num\">" + esc(item.detail) +
        "</td><td class=\"num\">" + item.speed_km_per_min + " m/min</td><td class=\"num\">" +
        item.vo2max + "</td></tr>").join("") + "</tbody></table></div>"
    : '<p class="hint">No race result, threshold pace or long-run time was given, so the zones are ' +
      "anchored on the goal time alone. Add one and they sharpen.</p>";
  const zones = fitness.zones.map((zone) =>
    '<div class="zone"><div><b>' + esc(zone.label) + '</b> <small>' + zone.lo_pct + "-" +
    zone.hi_pct + "% VO2max</small><br><small>" + esc(zone.purpose) + "</small></div>" +
    '<div class="num">' + esc(zone.fast) + " - " + esc(zone.slow) + " /" + paceUnit(plan) + "</div></div>").join("");
  return '<div class="kv"><div><b>' + fitness.vo2max + '</b><span>estimated VO2max</span></div>' +
    '<div><b>' + esc(fitness.vo2max_source) + '</b><span>evidence used</span></div>' +
    '<div><b>' + fitness.goal_fraction + '%</b><span>goal pace as % VO2max</span></div>' +
    '<div><b>' + esc(fitness.goal_zone) + '</b><span>zone the goal sits in</span></div></div>' +
    "<h3>Training zones</h3>" + zones +
    "<h3>Evidence</h3>" + evidence;
}

function paceUnit(plan) {
  return plan.pace.unit === "km" ? "km" : "mi";
}

function renderFuel(plan) {
  const fuel = plan.fueling;
  const weather = plan.weather;
  const rows = fuel.checkpoints.map((point) =>
    "<tr><td>" + point.unit + "</td><td class=\"num\">" + (point.clock || "-") +
    "</td><td class=\"num\">" + point.minutes + "</td><td class=\"num\">" + point.carbs_g +
    " g</td><td class=\"num\">" + point.fluid_l + " L</td><td class=\"num\">" + point.sodium_mg +
    " mg</td><td>" + esc(point.action) + "</td></tr>").join("");
  const items = fuel.per_hour_items.map((item) =>
    "<tr><td>" + esc(item.name) + "</td><td class=\"num\">" + item.carbs_g +
    " g</td><td class=\"num\">" + item.per_hour + "</td><td>" + esc(item.note) + "</td></tr>").join("");
  const heat = weather.heat_index;
  return '<div class="kv">' +
    '<div><b>' + fuel.carb_target_g_h + ' g/h</b><span>carbohydrate (' +
      fuel.carb_range_g_h[0] + "-" + fuel.carb_range_g_h[1] + ")</span></div>" +
    '<div><b>' + fuel.fluid_target_l_h + ' L/h</b><span>fluid (cap ' + weather.fluid_cap_l_h + " L/h)</span></div>" +
    '<div><b>' + fuel.sodium_mg_h + ' mg/h</b><span>sodium</span></div>' +
    '<div><b>' + weather.sweat_rate_l_h + ' L/h</b><span>sweat rate (' +
      (weather.sweat_measured ? "measured" : "estimated") + ")</span></div>" +
    '<div><b>' + fuel.kcal_per_hour + '</b><span>kcal per hour</span></div>' +
    '<div><b>' + (heat.heat_index_f === null ? "unknown" : Math.round(heat.heat_index_f) + " F") +
      '</b><span>heat index (' + esc(heat.risk) + ")</span></div></div>" +
    (heat.advice ? '<p class="hint">' + esc(heat.advice) + "</p>" : "") +
    "<h3>Fuel checkpoints</h3>" +
    '<div class="tr"><table><thead><tr><th>At</th><th class="num">Clock</th><th class="num">Min</th>' +
    '<th class="num">Carbs</th><th class="num">Fluid</th><th class="num">Sodium</th><th>Action</th>' +
    "</tr></thead><tbody>" + rows + "</tbody></table></div>" +
    "<h3>What to carry</h3>" +
    '<div class="tr"><table><thead><tr><th>Item</th><th class="num">Carbs</th><th class="num">Per hour</th>' +
    "<th>Note</th></tr></thead><tbody>" + items + "</tbody></table></div>" +
    "<h3>Before the start</h3><ul class=\"clean\">" +
    "<li>" + fuel.pre_race.carbs_24h_g + " g of carbohydrate the day before (" +
      Math.round(fuel.pre_race.carbs_24h_g / fuel.weight_kg) + " g/kg)</li>" +
    "<li>" + fuel.pre_race.carbs_1_4h_g + " g in the last few hours</li>" +
    "<li>" + fuel.pre_race.fluid_2_4h_l + " L of fluid 2-4 hours out</li>" +
    "<li>" + fuel.pre_race.caffeine_mg + " mg caffeine about 45 minutes before the gun</li></ul>";
}

function renderTraining(plan) {
  const training = plan.training_plan;
  if (!training.available) {
    return "<h3>No training plan</h3><p class=\"hint\">" + esc(training.reason) +
      "</p><p class=\"hint\">Add 'Weekly miles' and 'Long run' to the brief and the block is built " +
      "from your current volume: cutback weeks, a build phase and a three-week taper.</p>";
  }
  const rows = training.rows.map((row) =>
    "<tr><td>" + row.week + "</td><td>" + esc(row.phase) + "</td><td class=\"num\">" + row.weekly +
    " " + training.unit + "</td><td class=\"num\">" + row.long_run + " " + training.unit +
    "</td><td class=\"num\">" + row.long_run_pct + "%</td><td>" + esc(row.focus) + "<br><small>" +
    esc(row.sessions.join("  |  ")) + "</small></td></tr>").join("");
  return '<div class="kv"><div><b>' + training.weeks + '</b><span>weeks</span></div>' +
    '<div><b>' + training.baseline_weekly + ' ' + training.unit + '</b><span>now</span></div>' +
    '<div><b>' + training.peak_weekly + ' ' + training.unit + '</b><span>peak week</span></div>' +
    '<div><b>' + training.peak_long_run + ' ' + training.unit + '</b><span>peak long run</span></div>' +
    '<div><b>' + (training.cutback_weeks.join(", ") || "none") + '</b><span>cutback weeks</span></div>' +
    '<div><b>' + training.taper_weeks.join(", ") + '</b><span>taper weeks</span></div></div>' +
    '<div class="tr"><table><thead><tr><th>Week</th><th>Phase</th><th class="num">Weekly</th>' +
    '<th class="num">Long run</th><th class="num">% distance</th><th>Focus</th></tr></thead><tbody>' +
    rows + "</tbody></table></div>";
}

function renderFindings(plan) {
  if (!plan.findings.length) {
    return "<h3>No findings</h3><p class=\"hint\">Nothing in the brief raised a flag.</p>";
  }
  const order = { critical: 0, high: 1, medium: 2, low: 3, info: 4 };
  const sorted = plan.findings.slice().sort((a, b) => order[a.severity] - order[b.severity]);
  const items = sorted.map((finding) =>
    '<div class="finding ' + finding.severity + '"><h4><span class="sev">' + finding.severity +
    "</span>" + esc(finding.title) + '</h4><p><b>Evidence:</b> ' + esc(finding.evidence) +
    "</p><p><b>Why it matters:</b> " + esc(finding.why) + "</p><p><b>Fix:</b> " + esc(finding.fix) +
    "</p></div>").join("");
  return "<h3>" + plan.findings.length + " finding" + (plan.findings.length === 1 ? "" : "s") +
    "</h3>" + items;
}

function renderFixes(plan) {
  if (!plan.priority_actions.length) {
    return "<h3>Nothing to fix</h3><p class=\"hint\">The brief is clean.</p>";
  }
  const items = plan.priority_actions.map((action, index) =>
    "<li><b>" + (index + 1) + ". " + esc(action.title) + "</b> <span class=\"sev\">" + action.severity +
    "</span> " + esc(action.axis) + " (~" + action.estimated_gain + " points)<br>" + esc(action.fix) +
    "</li>").join("");
  return "<h3>Do these first</h3><ul class=\"clean\">" + items + "</ul>";
}

function renderNotes(plan) {
  return "<h3>Strengths</h3><ul class=\"clean\">" +
    plan.strengths.map((note) => "<li>" + esc(note) + "</li>").join("") +
    "</ul><h3>Model notes</h3><ul class=\"clean\">" +
    plan.model_notes.map((note) => "<li>" + esc(note) + "</li>").join("") + "</ul>";
}

async function savePlan() {
  const brief = $("brief").value.trim();
  if (!brief) { showError("Nothing to save - build a plan first."); return; }
  const rating = $("rating").value;
  try {
    const data = await api("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        brief: brief,
        review_profile: $("reviewProfile").value,
        rating: rating === "" ? null : Number(rating),
        notes: $("notes").value
      })
    });
    lastRunId = data.run.id;
    $("saveMsg").textContent = "Saved as run #" + lastRunId;
    $("expMd").href = "/api/runs/" + lastRunId + "/export?format=md";
    $("expJson").href = "/api/runs/" + lastRunId + "/export?format=json";
    $("expCsv").href = "/api/runs/" + lastRunId + "/export?format=csv";
    loadRuns();
  } catch (err) {
    showError(err.message);
  }
}

async function loadRuns() {
  try {
    const data = await api("/api/runs");
    const list = $("runList");
    if (!data.runs.length) {
      list.innerHTML = '<p class="hint">No saved plans yet.</p>';
      return;
    }
    list.innerHTML = data.runs.map((run) => {
      const stars = [1, 2, 3, 4, 5].map((value) =>
        '<button data-run="' + run.id + '" data-rating="' + value + '" class="' +
        (run.rating && run.rating >= value ? "on" : "") + '">*</button>').join("");
      return '<div class="run"><div><b>' + esc(run.race_name) + "</b> " + esc(run.goal_time) +
        '<div class="meta">' + esc(run.distance) + " | " + run.overall + "/100 (" +
        esc(run.verdict) + ") | " + esc((run.created_at || "").slice(0, 16)) +
        (run.notes ? " | " + esc(run.notes) : "") + "</div></div>" +
        '<div class="stars">' + stars + "</div></div>";
    }).join("");
    Array.from(list.querySelectorAll(".stars button")).forEach((button) => {
      button.addEventListener("click", async () => {
        const id = button.dataset.run;
        const rating = Number(button.dataset.rating);
        try {
          await api("/api/runs/" + id + "/rating", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ rating: rating })
          });
          loadRuns();
        } catch (err) { showError(err.message); }
      });
    });
  } catch (err) { /* history is optional */ }
}

init();
