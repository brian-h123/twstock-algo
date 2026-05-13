# Dependency Rationale

| Package | Type | Added | Why |
|---|---|---|---|
| `pandera` | runtime | Phase 1 | Schema validation at source/storage boundaries (decision D5) |
| `pyarrow` | runtime | Phase 1 | Parquet I/O for the per-ticker storage layout (decision D1) |
| `duckdb` | runtime | Phase 1 | SQL queries across the parquet store via `storage.query()` |
| `requests` | runtime | Phase 1 | FinMind HTTP calls — no SDK, keeps responses inspectable for VCR |
| `python-dotenv` | runtime | Phase 1 | Load `FINMIND_TOKEN` from `.env` without leaking it into the shell |
| `exchange_calendars` | runtime | Phase 1 | XTAI trading-day calendar for DQ missing-day checks (decision D8) |
| `pytest-recording` | dev | Phase 1 | VCR cassette tests for source adapters (decision D6); pulls in `vcrpy` |
| `tqdm` | runtime | Phase 1 | Progress bar for the backfill script |
