# Race Pace Forge

**Local-first race pacing, fueling and training planner.** Paste a race brief; get
grade-adjusted splits with clock times, a fueling and hydration schedule placed on your
actual aid stations, a periodised training block, and a 0-100 readiness score with the
fixes worth doing first.

No API keys. No network calls. No LLM. Standard library plus Flask.

---

## What it does

Given a race brief (distance, goal time, course, weather, and whatever training history you
have), Race Pace Forge answers the questions a runner actually has before race day:

- **Is this goal time real?** The goal pace is compared against your recent race, your
  threshold pace and your long-run pace. When the goal implies a fitness you have not
  demonstrated, it says so and caps the score.
- **What pace do I hold, and when?** Splits are adjusted for terrain using the published
  energy cost of running on a gradient, so uphill splits are slower, downhill splits faster,
  and the whole table still sums to your goal time. Clock times are attached to every split.
- **How do I split it?** Three strategies with checkpoints: even effort, a negative split
  and a conservative start.
- **What do I eat and drink?** Carbohydrate, fluid and sodium targets scaled to your goal
  time, sweat rate and heat index, with checkpoints placed on your real aid stations and an
  item list (gels, chews, drink, bars, rice balls, potatoes) converted into per-hour counts.
- **Can I train for it?** A periodised block built from your current weekly volume, with
  cutback weeks, a build phase and a three-week taper, plus a long-run progression toward
  the race distance.
- **What is going to bite me?** A findings engine where every item carries the evidence that
  triggered it, why it matters and a concrete fix.

## Why it matters

Most runners pick a goal time from a calculator, a pace in a training log, or a feeling.
All three are wrong in predictable ways, and the mistakes only show up at mile 32. The
expensive part of a race is decided by four numbers: the goal pace, the carbohydrate rate,
the fluid rate and the training behind them. This tool makes those four numbers explicit,
checks them against each other, and shows what to change first.

## The models used

All of these are published, standard and computed locally:

| Model | Used for | Source |
|---|---|---|
| Energy cost of running on a gradient | Uphill/downhill split adjustment | Minetti et al. (2002) |
| Running oxygen cost VO2 = 0.2v + 0.9v*grade + 3.5 | VO2max estimate, zone paces, energy cost | ACSM |
| VO2max = 0.2v + 0.8 for a race run near max effort | Fitness evidence from a recent race | Daniels running formula |
| Race vs goal intensity fractions | Anchoring a goal time or a race result to VO2max | Racing-intensity conventions |
| Heat index (Rothfusz regression) | Heat risk band, pace advice, sodium and fluid | NOAA / US National Weather Service |
| Endurance fuelling rates | Carbohydrate per hour by duration | Standard fuelling guidance (30-90 g/h) |
| Sweat rate from temperature and humidity | Fluid and sodium targets | Sports-medicine practice |

**Honest limitations.** The ACSM running equation is a population fit and is conservative
for well-trained runners; treat the VO2max and zone paces as a good estimate rather than a
laboratory result. Fractions of VO2max depend on the intensity anchors in the code and will
move if you edit them. Course elevation is interpolated linearly between the points you give
it, and when no profile is supplied a representative profile is assumed and flagged. Nothing
here is medical advice, and an injury on the brief is treated as a reason to see a clinician,
not as a number to optimise.

## Quick start

    cd race-pace-forge
    python3 -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    python3 app.py
    # open http://127.0.0.1:5000

Set `PORT` to change the port, `HOST` to bind elsewhere, `RACEPACE_DB` to move the
SQLite file.

## The brief format

One `Key: value` per line. Only `Goal time` is required.

    Race: Chicago Marathon 2027
    Distance: marathon          # 5k | 10k | half | marathon | 50k
    Goal time: 3:30:00         # 3:30:00, 3:30, 3h30m, 210 min
    Date: 2027-10-10
    Start time: 07:30
    Course: rolling hills       # flat | rolling | hilly | finish hill | trail
    Elevation: 0/0, 10/130, 20/210, 26/150   # marker/feet pairs
    Aid stations: 4, 9, 14, 19, 24 miles
    Weight: 75 kg               # lb also works
    Age: 38
    Level: intermediate
    Recent race: 10k in 44:00
    Weekly miles: 45
    Long run: 20 miles in 1:42:00
    Threshold pace: 7:15       # per mile or km, matching Units
    Sweat rate: 0.9            # litres per hour, measured
    Injuries: left calf strain 2 weeks ago
    Temp: 14C                  # F also works
    Humidity: 65%
    Wind: 10 kph
    Plan weeks: 18
    Units: mi                  # mi (default) or km

## Review profiles

The review profile re-weights the five axes (goal realism, pace and course plan, fuelling
and hydration, training support, race-day readiness):

- **General race planning** - even weighting.
- **First marathon** - training support and goal realism carry more.
- **Marathon target time** - goal realism dominates; the plan is judged on whether the time is earned.
- **Ultra / 50K** - fuelling and hydration carry more.
- **Hot weather race** - race-day readiness and fuelling dominate.
- **Race-day execution** - the pace plan and race-day readiness dominate.

## Scorecard and caps

Each finding carries a severity (critical, high, medium, low, info) and a penalty against
one axis. Structural caps stop a plan from scoring well by skipping the hard part:

- a critical goal finding caps goal realism at 25 and the overall score at 40;
- a goal that outruns your long-run evidence caps realism at 45 and the overall at 60;
- a fuelling gap caps fuelling at 45 and the overall at 70;
- heat in the danger band caps race-day readiness at 50;
- an unresolved injury caps race-day readiness at 40 and the overall at 45;
- an axis that could not be assessed caps the overall score and says why;
- a brief with neither a result nor training data cannot be called race ready.

Verdicts: `race_ready` (85+), `mostly_ready` (70+), `needs_work` (55+),
`tight_but_usable` (40+), `not_ready` (below 40).

## API

| Method | Route | Purpose |
|---|---|---|
| GET | `/` | The app |
| GET | `/api/health` | Liveness |
| GET | `/api/profiles` | Review profiles and their weights |
| GET | `/api/samples` | Built-in example briefs |
| POST | `/api/analyze` | `{"brief": "...", "review_profile": "general"}` -> full plan |
| GET | `/api/runs` | Saved plans |
| POST | `/api/runs` | Save a plan (optional `rating` 1-5 and `notes`) |
| GET | `/api/runs/<id>` | One saved plan |
| POST | `/api/runs/<id>/rating` | Rate a saved plan |
| DELETE | `/api/runs/<id>` | Delete a saved plan |
| GET | `/api/runs/<id>/export?format=md|json|csv` | Markdown plan, JSON plan, splits CSV |

## Layout

    engine.py     the analysis engine (parsing, physics, findings, scoring)
    db.py         SQLite persistence: saved plans and self-ratings
    app.py        Flask API, markdown/CSV/JSON exports
    static/       dark vanilla-JS front end (no build step, no framework)
    samples/      six built-in briefs used as calibration cases
    tests/        engine, API and frontend contract tests

## Tests

    python3 -m unittest discover -s tests -t .

36 tests covering duration parsing and unit conversion, the Minetti cost curve, uphill and
downhill split ordering, split totals summing to the goal time, strategy half-ordering, zone
ordering, fuelling rates by duration, the structural caps, every finding carrying evidence
and a fix, the API lifecycle, exports, and the HTML/JS field contract.

## Calibration

The six built-in briefs are the calibration set:

| Sample | Expected behaviour |
|---|---|
| `chicago-marathon-330` | A well-prepared 3:30 marathon: the control case, no critical findings |
| `first-marathon-305` | A 3:05 first marathon on 22 miles a week: goal outruns the evidence, capped |
| `desert-50k-heat` | 50K in 33 C with a calf strain two weeks old: heat, injury and fuelling all fire |
| `heat-wave-10k` | A hot afternoon 10K with a 5K-speed goal: heat and fuelling findings |
| `injured-return-half` | A half eight weeks out with an unresolved shin: injury and plan-length findings |
| `novice-5k` | Goal time and nothing else: training not assessed, score provisional |

## Not a coach

This is a planning tool. It does not know your history, your body, or your life. Talk to a
coach about training, and to a clinician about anything that hurts.

## License

MIT.
