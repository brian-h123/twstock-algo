# Phase 0 — Foundation Setup

**Goal:** establish the repo, tooling, and conventions so every subsequent phase has somewhere to land. No business logic in this phase — just scaffolding and a single tiny piece of real code (`universe.py`) to prove the wiring works.

**Estimated duration:** 1–3 days at 4–5 hours/day.

---

## Exit Criteria — Definition of Done

Phase 0 is complete when **all** of the following are true:

- [x] Private GitHub repo `twstock-algo` exists, is cloned locally, and has an MIT `LICENSE` file
- [x] `uv` is initialized with Python 3.12 pinned in `.python-version`
- [x] `pyproject.toml` declares the project and lists initial dependencies (see below)
- [x] Folder skeleton matches `CLAUDE.md` section 4 exactly, with `__init__.py` in every Python package
- [x] `CLAUDE.md` is at repo root (already drafted; copy in)
- [x] `PHASE_0.md` is at repo root (this file)
- [x] `README.md` covers: project elevator pitch, current phase, setup instructions, a "this is a personal project, not investment advice" disclaimer
- [x] `.gitignore` covers Python, uv, IDE files, `data/`, `.env`, broker certificates, `__pycache__`, `.pytest_cache`, `.ruff_cache`
- [x] `.env.example` lists `FINMIND_TOKEN`, `FUBON_USER_ID`, `FUBON_PASSWORD`, `FUBON_CERT_PATH`, `FUBON_CERT_PASSWORD` with empty values
- [x] `.pre-commit-config.yaml` runs ruff (lint + format) and a pytest smoke test
- [x] `pre-commit run --all-files` passes cleanly on a fresh repo
- [x] `uv run pytest` runs successfully (one trivial test in `tests/test_smoke.py` is fine)
- [x] `src/twstock/config.py` exists with at least: `PROJECT_ROOT`, `DATA_DIR`, `UNIVERSE_SNAPSHOT_DATE`, `BACKTEST_CAPITAL_NTD = 1_000_000`, `MAX_POSITION_PCT = 0.10`
- [x] `src/twstock/universe.py` exposes `get_top50_universe() -> list[str]` returning a hard-coded snapshot of TWSE 0050 ETF constituents (50 ticker strings, e.g. "2330", "2317", ...)
- [x] Test exists in `tests/test_universe.py` asserting `len(get_top50_universe()) == 50` and all entries match `r"^\d{4}$"`
- [x] `src/twstock/data/sources/base.py` defines the abstract `DataSource` class with the three methods listed in `CLAUDE.md` section 5 (signatures only, `NotImplementedError` bodies)
- [x] `src/twstock/data/sources/finmind.py` and `src/twstock/data/sources/twse.py` exist as empty subclasses raising `NotImplementedError` (placeholders for Phase 1)
- [x] `journal/README.md` documents the journal conventions
- [x] `journal/weekly/2026-W19.md` exists with this week's plan and a dated section for each working day
- [x] `journal/decisions/0001-stack-decisions.md` exists, summarizing the stack lock-ins from `CLAUDE.md` section 2
- [x] First commit pushed to GitHub

## Initial Dependencies

Runtime:
- `pandas`
- `pyarrow`            # parquet engine
- `duckdb`
- `requests`           # for FinMind / TWSE HTTP
- `python-dotenv`
- `pandera`            # schema enforcement (used in Phase 1)

Dev:
- `pytest`
- `ruff`
- `pre-commit`

Streamlit and Plotly are deferred to Phase 2; vectorbt to Phase 3; FinMind SDK to Phase 1 — install when needed, not preemptively.

## Universe Snapshot

For `get_top50_universe()`, fetch the current 0050 constituent list from a public source (e.g. the Yuanta 0050 ETF holdings page or TWSE) and hard-code the 50 tickers as a Python list. Comment the list with the snapshot date. Do not implement live fetching in this phase.

## Suggested Daily Breakdown

### Day 1 — Repo and tooling (~3 hours)
- Create private GitHub repo, add MIT license
- `uv init`, set Python 3.12, create `pyproject.toml` with deps above
- Create folder skeleton with all `__init__.py` files
- Drop in `CLAUDE.md`, `PHASE_0.md`, `README.md`
- Set up `.gitignore`, `.env.example`
- Configure ruff in `pyproject.toml`, set up `.pre-commit-config.yaml`
- Write `tests/test_smoke.py` with `assert True`
- Verify `uv run pytest` and `pre-commit run --all-files` both pass
- First commit and push

### Day 2 — First real code (~3 hours)
- Implement `src/twstock/config.py`
- Implement `src/twstock/universe.py` with hard-coded Top 50 list (look up current 0050 holdings)
- Write `tests/test_universe.py`
- Implement `src/twstock/data/sources/base.py` (abstract class only)
- Stub `finmind.py` and `twse.py` (subclasses raising `NotImplementedError`)
- Verify everything passes

### Day 3 — Buffer and journal (~2–3 hours)
- Set up `journal/` folder, write `journal/README.md`
- Write `journal/weekly/2026-W19.md` with the week plan and dated sections for each working day so far
- Write `journal/decisions/0001-stack-decisions.md`
- Polish `README.md`
- Register for FinMind API token (free tier) and add to local `.env`
- Pursue Sinopac account opening in parallel (independent of code progress)

## Out of Scope for Phase 0

Explicitly **not** part of this phase:
- Any actual data fetching (Phase 1)
- Any chart / dashboard code (Phase 2)
- Any indicator computation (Phase 4)
- Broker code or simulator (Phase 5)
- CI/CD pipeline (deferred to a later polish phase)
- Docker (deferred unless a clear need emerges)

If a Phase 0 task tempts you toward any of the above, stop and write it as a Phase 1+ ticket in the journal instead.
