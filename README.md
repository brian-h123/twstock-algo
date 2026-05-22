# twstock-algo

An algorithmic trading pipeline for the Taiwan Stock Exchange (TWSE), built as a portfolio
piece for data science / ML engineering job applications.

The project incrementally builds a full pipeline:
data ingestion → inspection dashboard → backtesting engine → technical-indicator strategies →
paper-trading simulator → ML-based strategies → alternative-data strategies → productionization.

**Current phase:** Phase 3 — Backtesting Engine (Phase 2 complete; see `journal/phase_2_retro.md`)

---

## Setup

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12.

```bash
git clone https://github.com/brian-h123/twstock-algo.git
cd twstock-algo
uv sync
uv run pre-commit install
```

Copy `.env.example` to `.env` and fill in your API credentials:

```bash
cp .env.example .env
# then edit .env with your FINMIND_TOKEN etc.
```

### Run tests

```bash
uv run pytest
```

### Lint / format

```bash
uv run ruff check src tests
uv run ruff format src tests
```

---

## Run the dashboard

Requires all 50 tickers to be backfilled (Phase 1). Then:

```bash
uv run streamlit run src/twstock/ui/app.py
```

This opens the inspection dashboard at `http://localhost:8501`. Select any Top 50 ticker from the sidebar, adjust the date range, and toggle between adjusted and raw prices to visually verify the Phase 1 adjustment math.

---

## Disclaimer

This is a personal learning project. **Nothing in this repository constitutes financial or
investment advice.** Past simulated performance does not guarantee future results.
All strategies are for educational purposes only. Trade at your own risk.
