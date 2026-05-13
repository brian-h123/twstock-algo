# ADR: Adjustment cross-check method (supersedes D9)

**Date:** 2026-05-13
**Status:** Accepted
**Supersedes:** Decision D9 in `PHASE_1.md` (Yahoo Finance reference fixture)

## Context

The original Phase 1 plan, in Decision D9, called for a one-time manual CSV
download from Yahoo Finance's historical-data page, committed as
`tests/fixtures/2330_yahoo_adjusted.csv`, to serve as the reference adjusted
close series for cross-validating the in-house adjustment function over the
full 2016-2026 window.

Two facts now make that plan unworkable:

1. **Yahoo Finance has paywalled historical CSV downloads.** Bulk download
   is a Yahoo Finance Gold feature; this also broke `yfinance` and similar
   tools that previously scraped the same endpoint. The browser display of
   historical prices still works, but copying that to a CSV-committed fixture
   for a portfolio repo would amount to circumventing the paywall.
2. **Stooq does not cover the Taiwan Stock Exchange.** Stooq was the
   reflexive replacement candidate; verification of its supported markets
   (Infront-sourced: LSE, Nasdaq, NYSE, NYSE MKT, WSE) confirms TWSE is not
   in scope. The earlier assumption that `2330.tw` would resolve there was
   incorrect.

Other candidates evaluated:

- **Investing.com / stockanalysis.com:** Coverage exists, but free-tier
  bulk CSV export status is uncertain and ToS for committing the data to a
  public repo is friction.
- **TSMC ADR (TSM on NYSE) via any free US source:** Different security
  (ADR ratio 5:1, USD, ADR fees, T+1 settlement offset). Total-return
  parity holds over multi-year windows but daily adjusted close does not
  map cleanly.
- **Fubon Neo desktop charting export:** Authoritative for Taiwan-market
  back-adjustment convention, but not reproducible by anyone outside the
  developer's broker login.
- **TWSE official:** Does not publish back-adjusted historical close.

## Decision

Replace the bulk-CSV cross-check with a **local-invariant test against
hand-curated anchor events**, sourced from primary documents.

For each anchor event with ex-date T, dividend D, raw close on T-1 of X,
and raw close on T of Y, the correctness invariant any back-adjustment
implementation must satisfy is:

    adj_close[T-1] / adj_close[T]  ≈  X / (Y + D)

Equivalently, the back-adjustment factor applied at the event is exactly:

    factor_T  =  Y / (Y + D)

(This is the standard Taiwan 還原權息 convention for cash dividends; the
analogous local invariants for stock dividends, splits, and rights issues
are documented in `data/adjustments.py`.)

The anchor fixture (`tests/fixtures/anchor_dividends.json`) records the
following for each anchor event:

- `ticker`, `ex_date`, `action_type`
- `dividend_amount` — sourced from TSMC SEC 6-K filings and TSMC IR
  (the SEC filings are particularly clean: they are filed in English with
  the per-common-share NT$ figure to 8 decimal places and are completely
  independent of any Taiwan-market data vendor)
- `raw_close_t_minus_1` and `raw_close_t` — fetched from TWSE's official
  `exchangeReport/STOCK_DAY` endpoint, which publishes daily close prices
  for each ticker (these are the same prices FinMind ultimately ingests,
  but obtained via a completely separate pipeline, which is the relevant
  notion of independence)
- `expected_back_adj_factor` — precomputed `Y / (Y + D)`

The cross-validation test in `tests/data/test_api.py` asserts that, for
each anchor event, the user's adjusted close series produces the expected
ratio across the event within 0.01% tolerance (tighter than the original
plan's 0.1% because we are no longer dependent on a third party's rounding
or adjustment-policy choices).

## Why this is stronger, not weaker

1. **Provenance is clean.** Each test point traces to a primary document
   (SEC filing or TWSE published quote). A reviewer can independently
   verify any anchor in ~5 minutes.
2. **Failure is diagnostic.** If the user's function is wrong at one
   event type but right at others, the failing anchor identifies exactly
   which event-type math is broken. A bulk-CSV comparison only tells you
   "something somewhere differs by 0.X%."
3. **No ToS friction.** Both data sources (SEC EDGAR, TWSE) are public
   government / regulator publications.
4. **Reproducible from the repo.** A teammate or future-self can run
   `scripts/build_anchors.py` and regenerate the fixture from scratch.
5. **Less breadth, but breadth wasn't the goal.** The bulk-CSV approach
   tests ~2500 row-by-row comparisons, but adjustment-math failure modes
   cluster around corporate-action events. Testing 6 events catches every
   bug a 2500-row comparison would catch except small floating-point drift
   that is not actually meaningful.

## Anchor selection

Six TSMC events spanning 2018-2024 are included in the initial fixture:
2 from the annual-dividend era (2018, 2019), 2 from the early quarterly
era (2020, 2022), and 2 from the recent quarterly era (2023, 2024). All
are cash-dividend events.

**Gap acknowledged:** TSMC has not had a stock dividend or split since
2008, so the initial fixture does not exercise those code paths. A
second ticker with stock-dividend history (candidates: 2317 Hon Hai,
1101 Taiwan Cement, 1216 Uni-President) should be added as anchor
coverage in a follow-up before Phase 1 exit. Tracking item:
`journal/phase_1_followups.md` (to be created).

## Implementation impact

- `PHASE_1.md` D9 rewritten; §4.6 specifies the anchor approach; §9 exit
  criterion replaced; §13 kickoff checklist updated.
- `CLAUDE.md` updated to reflect the new cross-validation fixture path.
- New file `scripts/build_anchors.py` regenerates the JSON fixture from
  the dividend reference table and live TWSE queries.
- No new Python dependencies (`requests` is already in scope per §12).

## Open questions

1. Should the fixture include the raw `expected_back_adj_factor` as well
   as the inputs needed to recompute it? Including it is redundant but
   makes the JSON easier to eyeball-debug. **Resolved: include it.**
2. Tolerance: 0.01% or tighter? Float64 precision allows ~1e-12 here, but
   TWSE publishes prices to 2 decimal places only, so realistic precision
   floor is ~5e-5 (half of 0.01 / minimum close ≈ 100). **Resolved: 0.01%.**
3. Cadence for refreshing anchors: never, on-Phase-exit, or on every CI
   run? Stale anchors are fine since dividends are immutable historical
   facts. **Resolved: regenerate manually only if the dividend reference
   table is edited; never automatically.**

---

## Amendment — 2026-05-13 (same day, post-review)

During first review of the fixture produced by `scripts/build_anchors.py`,
a footgun was identified in the precomputed convenience field.

The original spec stored `expected_back_adj_factor` = `Y / (Y + D)`. This
is the textbook back-adjustment factor — the multiplicative correction
applied to all pre-event raw prices. It is a mathematically meaningful
quantity, but it is *not* the value the test asserts. The test asserts the
ratio of adjusted closes across the event:

    adj_close[T-1] / adj_close[T]  =  X / (Y + D)

These two values (`Y / (Y + D)` vs `X / (Y + D)`) differ unless `X == Y`,
which never happens in practice. A test author who used the precomputed
field as the assertion target without rederiving the math would have
written a test that consistently fails when the code is correct — by a
magnitude equal to the daily price move at the event.

**Change applied:**

- Field renamed `expected_back_adj_factor` → `expected_adj_close_ratio`.
- Stored value changed from `Y / (Y + D)` to `X / (Y + D)`.
- The field is now exactly the value the §4.6 test contract asserts; no
  rederivation needed in the test implementation.

**Why this is not just cosmetic:** the rename eliminates a class of silent
test-implementation bugs where a future contributor (or a future Claude
Code session that doesn't re-read the math) reaches for the precomputed
value as the assertion target. With the renamed field, the most obvious
usage is also the correct usage.

Also fixed alongside (both cosmetic, neither affecting correctness):

- Unused `field` import from `dataclasses` removed.
- `datetime.utcnow()` (deprecated in Python 3.12) replaced with
  `datetime.now(UTC)` and `from datetime import UTC` added.

PHASE_1.md §4.6 updated in lockstep; the test contract code-block in that
section was already specified in terms of the ratio (not the factor) and
required no change.

**Process observation worth noting in the next weekly retro:** the
original Anchor dataclass had a misleadingly-named convenience field. The
underlying data (raw closes + dividend) was sufficient on its own — the
precomputed field was added for eyeball-debug ergonomics but introduced a
trap. Lesson: when precomputing convenience values into a fixture, make
sure each one is *exactly* what some consumer will use directly; if there
are two related quantities (factor vs ratio in this case), the temptation
is to pick "the textbook one" rather than "the one your test asserts."
The latter is the right pick.
