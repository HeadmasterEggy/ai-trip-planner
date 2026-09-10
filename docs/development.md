# Development environment

## Local setup

```bash
uv sync
uv run streamlit run streamlit_app.py     # http://localhost:8501
```

Copy `.env.example` to `.env` for live models. Keep real credentials there and never commit them —
`.env` is ignored, `.env.example` is the tracked template.

The app runs with no keys at all: every specialist falls back to deterministic output and the tool
ports serve fixtures, so the whole loop including conflict detection and escalation is exercised
offline.

## Checks

```bash
uv run pytest                    # 112 tests, no network
uv run ruff check .
uv run ruff format --check .
```

`.github/workflows/checks.yml` runs exactly those on 3.11, 3.12 and 3.13 for every push to `main`
and every pull request, so a change is verified by the offline path rather than by whoever
remembered to run it.

The default run never reaches a provider: anything that would — a live model, a real maps call — sits
behind an injected port or an explicit `specialists=` argument. That keeps the suite deterministic and
free, and it is also its blind spot: three defects in `docs/debugging-log.md` existed *only* with a key
configured.

## Live provider checks

The one path the offline suite cannot exercise has its own opt-in tests:

```bash
DEEPSEEK_API_KEY=... RUN_LIVE_TESTS=1 uv run pytest -m live
```

Two cheap calls, no five-specialist fan-out. They cover what a scripted model cannot: that the
provider still accepts the structured-output arrangement `models.py` documents (function calling with
thinking disabled, because DeepSeek rejects both the json_schema response format and a forced
`tool_choice` in thinking mode), and that it still calls a delegation tool that takes no arguments —
which is the one thing item 1.2 of `docs/framework-alignment.md` could not verify offline.

## Verifying a deployment change

Streamlit Community Cloud installs from `requirements.txt` and does **not** install this project,
so a `src/` layout is not importable there. A local editable install hides that completely. Check
against an environment that resembles the cloud rather than trusting a local run:

```bash
python3 -m venv /tmp/cloudsim
/tmp/cloudsim/bin/pip install -r requirements.txt
/tmp/cloudsim/bin/python -c "
import sys; sys.path.insert(0, 'src')
from trip_planner.workflow import run_orchestrator
from trip_planner.demo import DEMO_BRIEF
print(run_orchestrator(DEMO_BRIEF).round)
"
```

## Python version

`pyproject.toml` requires 3.11+. Streamlit Community Cloud offers 3.9–3.13, so pick **3.13** there.
Local development on 3.14 is fine; the code is checked against a 3.11 parse target.
