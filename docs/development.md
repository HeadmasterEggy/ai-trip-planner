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
uv run pytest            # 43 tests, no network
uv run ruff check .
uv run ruff format .
```

The tests never reach a provider. Anything that would — a live model, a real maps call — is behind
an injected port or an explicit `specialists=` argument, so CI stays deterministic and free.

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
