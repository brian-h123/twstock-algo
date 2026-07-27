# 0012 — Rights-Issue and Capital-Reduction Handling (D15 Resolution)

**Date:** 2026-07-24
**Status:** Accepted

## Context

`PHASE_5.md` D15 marked `RIGHTS_ISSUE` and `CAPITAL_REDUCTION` as "evidence-
gated with a loud assertion" — deliberately unmodeled, both `broker/
rejection.py::_ex_day_reference` (the ex-day price-limit reference) and
`broker/corporate_actions.py::decompose` (position mutations) raise
`ValueError` on either action type.

Building `scripts/phase_5_paper_run.py` (build-sequence step 15) surfaced
that this loud assertion is broader than intended. `ReplayFeed` always loads
the full Top-50 universe internally (`feed.py`), regardless of which
ticker(s) a `PaperSession` actually trades, and `BrokerSimulator.process_bar`
calls `price_limit_provider.observe(bar)` for every ticker in the batch —
not only tickers the session holds or trades. An unmodeled action anywhere
in the Top 50 during the replay window therefore crashes the run, even one
that never touches the affected ticker. Both action types are common enough
in the Top 50 (11 tickers had a rights issue between 2019–2025; three had a
capital reduction between 2021–2025 alone) that the canonical demonstrator
window (2330, 2021–2025, `PHASE_5.md §1`) cannot run at all without a fix
for both — this was confirmed empirically while validating the fix, not
assumed.

The two action types turned out to need genuinely different treatment,
not one shared fix:

**Rights issue** (現金增資) is a shareholder *choice*. Existing shareholders
get the right, not free shares, to subscribe `ratio` new shares per share
held at a `subscription_price`. A real shareholder can subscribe (pay cash,
receive shares), sell the rights on the market, or let them lapse (do
nothing, absorb the dilution uncompensated). That three-way ambiguity is
why D15 deferred this in the first place — the simulator can't silently
pick a behavior on the shareholder's behalf without it being a modeling
assumption someone decided on.

**Capital reduction** (減資), by contrast, turned out not to be ambiguous at
all once the data was examined. `data/sources/finmind.py::
_build_capital_reductions` always sets `cash_amount = NaN` for these rows —
this project's data source only ever captures the loss-absorbing,
share-cancelling kind, never a cash-distributing one. `ADR 0007` already
states this outright: "`STOCK_SPLIT` uses the same convention and the same
`_compute_factor` formula as `capital_reduction`." Structurally, in this
dataset a capital reduction *is* a (reverse) stock split — `ratio` follows
the identical `post_price / prior_price` convention, just typically >1
instead of <1 — and every shareholder's share count changes automatically.
There is no "shareholder does nothing" reading of this event the way there
is for a rights issue.

## Decision

**D1 — `_ex_day_reference` gets a `RIGHTS_ISSUE` branch using the TERP
formula, anchored on `prior_close` (not the ex-day's own close):**

```
reference_price = (prior_close + ratio * subscription_price) / (1 + ratio)
```

This is the standard TWSE ex-rights reference-price formula, and it must be
derivable from `prior_close` alone (unlike `adjustments.py`'s factor,
computed after the fact from the actual ex-day close) because the
price-limit band is needed at the ex-day's open, before that day's close
exists. It mirrors the existing `CASH_AND_STOCK_DIVIDEND` branch in the
same function (a cash term folded into the reference, divided by a share
multiplier) with the cash term's sign flipped — a rights issue brings cash
into the company rather than paying it out.

**D2 — `corporate_actions.py::decompose` treats `RIGHTS_ISSUE` as a lapse:
always a no-op, whether the position is held or flat.**

No share mutation, no cash mutation. This models the shareholder as
neither subscribing nor selling the rights. The resulting dilution is
already reflected for free: mark-to-market uses the raw market close,
which is real historical data and already trades at the diluted,
post-rights price level. No new bookkeeping is needed to represent the
dilution itself — only the reference-price fix (D1) was ever required to
stop the crash.

**D3 — `CAPITAL_REDUCTION` is folded into `STOCK_SPLIT`'s existing branches
in both `share_multiplier` and `decompose`/`_ex_day_reference`, not given
a lapse treatment.**

`share_multiplier` now accepts `CAPITAL_REDUCTION` alongside `STOCK_SPLIT`
with the identical formula (`1.0 / ratio`), since both share the same
`post_price / prior_price` ratio convention (`ADR 0007`). `decompose`
produces a real `ShareMutation` for it — not a no-op — because the share
count genuinely changes for every shareholder automatically, unlike a
rights issue's optional subscription. `_ex_day_reference` folds it into the
same branch as `STOCK_SPLIT`/`STOCK_DIVIDEND` (divide the reference by
`share_multiplier`), which is exactly `adjustments.py`'s
`capital_reduction` factor applied at the correct anchor point.

D15's "loud assertion" is now fully resolved for both action types —
`decompose` no longer raises for any "unmodeled" `action_type`; the only
remaining raises are missing/invalid field validation, the same as every
other action type.

*Rejected alternative (rights issue only):* model actual subscription
exercise (add `ratio × shares` new shares, deduct
`ratio × shares × subscription_price` cash). This is a materially bigger
change — it requires a subscription payment deadline not currently in the
corporate-actions schema (`pay_date` is dividend-specific), and an
insufficient-funds edge case with no obvious resolution (forced
forfeiture? a rejected mutation? a loan?). It also models a behavior most
passive, mechanically-driven strategies don't take in practice. Deferred
indefinitely, not merely to a later step.

*Rejected alternative (capital reduction only):* treat it as a lapse
no-op, matching the rights-issue treatment. Rejected once the data was
examined (see Context) — it would leave `LotLedger`'s share count and
`avg_cost` silently stale relative to the real, automatic share change
every shareholder experiences. That is a correctness bug, not an accepted
simplification.

## Consequences

- The canonical single-asset demonstrator (2330, 2021–2025) and any
  multi-year cross-sectional replay over the Top 50 can now run to
  completion without an unrelated ticker's rights issue or capital
  reduction crashing the session. Verified directly: a 2021–2023 window
  crashed on `CAPITAL_REDUCTION` before D3 and ran clean after.
- A position held through a rights-issue ex-date is not diluted by any
  explicit mutation — its share count and avg_cost are untouched. The
  dilution is implicit in the raw market price path, which is correct for
  real historical data but means `Account`/`LotLedger` state doesn't
  independently confirm the dilution the way O8 confirms a split's share
  mutation. This is an accepted gap: there is no cash or share entitlement
  to conserve under a lapse assumption, so no oracle-style invariant
  applies here the way it does for splits or dividends.
- A position held through a capital-reduction ex-date now gets a genuine
  `ShareMutation`, tested the same way `TestDecomposeStockSplit` tests
  `STOCK_SPLIT` — this is treated as load-bearing bookkeeping, not a
  simplification, since it's structurally identical to a split.
- If auto-subscribe modeling for rights issues is ever needed (e.g., a
  strategy that actively manages rights entitlements), it requires new
  schema (a subscription payment deadline) and buying-power handling at
  the moment of exercise — a follow-up ADR, not a reopening of this one.
- If this project's data source ever starts reporting a cash-distributing
  capital reduction (a genuine `cash_amount` populated on a
  `capital_reduction` row), D3's split-reuse treatment stops being correct
  and needs revisiting — not expected given `_build_capital_reductions`'s
  current behavior, but worth checking if the FinMind schema ever changes.

## Related

- Amends: `PHASE_5.md` D15 (rights/reductions evidence-gated with a loud
  assertion) — fully resolved for both action types (see D1–D3). `PHASE_5.
  md`'s own text is left as the historical planning record, per the
  precedent set by `ADR 0011` (which similarly didn't edit `PHASE_5.md`'s
  locked-decision table).
- Sibling: `ADR 0007` (stock split / corporate-action adjustment — the
  signal-side rights-issue and capital-reduction treatment in
  `data/adjustments.py` this ADR's formulas are derived from, applied at a
  different anchor point for the broker's forward-looking reference price).
- Discovered while building `scripts/phase_5_paper_run.py` (build-sequence
  step 15).
