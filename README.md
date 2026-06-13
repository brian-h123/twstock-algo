# twstock-algo

An algorithmic trading pipeline for the Taiwan Stock Exchange (TWSE), built as a portfolio
piece for data science / ML engineering job applications.

The project incrementally builds a full pipeline:
data ingestion → inspection dashboard → backtesting engine → technical-indicator strategies →
paper-trading simulator → ML-based strategies → alternative-data strategies → productionization.

**Current phase:** Phase 4 — Technical Strategies (Phase 3 complete; see `journal/phase_3_retro.md`)

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

## Run a backtest

Requires all 50 tickers (plus 0050) to be backfilled first. Then:

```python
from datetime import date
from twstock.backtest import Engine, TaiwanCostModel, BuyAndHold
from twstock.strategies.sma_crossover import SmaCrossover

engine = Engine(
    capital=1_000_000,
    cost_model=TaiwanCostModel(),
    max_position_pct=0.10,
    execution="next_open",
)

result = engine.run(
    strategy=SmaCrossover(fast=20, slow=60),
    tickers=["2330"],
    start=date(2020, 1, 1),
    end=date(2025, 12, 31),
)

benchmark = engine.run(
    strategy=BuyAndHold(),
    tickers=["0050"],
    start=date(2020, 1, 1),
    end=date(2025, 12, 31),
)

print(result.tear_sheet())
print(benchmark.tear_sheet())
```

The cost model is always on: 0.3% sell-side securities transaction tax (0.1% for ETFs), 0.0855% per-side commission, and 5 bps per-side slippage. Net return will be below gross return.

---

## Disclaimer

This is a personal learning project. **Nothing in this repository constitutes financial or
investment advice.** Past simulated performance does not guarantee future results.
All strategies are for educational purposes only. Trade at your own risk.
