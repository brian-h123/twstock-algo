"""State-machine adversarial fixtures (PHASE_5.md §6.4, step 12 -- state
machine group).

Every legal/illegal order transition already has dedicated coverage in
test_order.py, and the CANCEL transition itself is unit-tested against
BrokerSimulator directly in test_simulator.py. What's untested is the one
thing no strategy in Phase 5 ever triggers: a cancel. Cancelling is a
user-initiated action (a real trader hitting "cancel" on a resting order),
not something Strategy/CrossSectionalStrategy signals produce -- so this
fixture drives PaperSession's OWN broker/account/event_log directly,
confirming the session's reporting layer (not just BrokerSimulator's
internal mirror) absorbs a manually-triggered cancel correctly. There is no
FixtureFeed/strategy signal path that would exercise this, so
session.run() is deliberately not called here -- see test_events.py's
test_fill_before_submitted_is_a_known_gap_not_silently_wrong for the
sibling state-machine gap (out-of-order fold) that similarly falls outside
the FixtureFeed harness.

Cancelling a PARTIALLY_FILLED order is not reachable here: the simulator
has no resting orders (D9), so a partial fill is always immediately
followed by an EXPIRE within the same process_bar call
(test_simulator.py::test_partial_fill_immediately_followed_by_expire) --
there is no window where a partially-filled order is still cancellable.
The only realistic cancel window in Phase 5 is between submission and the
next process_bar call that would resolve it, which is exactly what this
fixture drives.
"""

from datetime import date
from decimal import Decimal

from twstock.backtest.cost_model import TaiwanCostModel
from twstock.backtest.strategy import BuyAndHold
from twstock.broker.account import fold
from twstock.broker.events import EventKind, OrderId, Side
from twstock.broker.feed import FixtureFeed
from twstock.broker.fill import FullFill
from twstock.broker.order import Order, OrderStatus, TimeInForce
from twstock.broker.session import PaperSession
from twstock.broker.settlement import NettingSettlement

_T1 = date(2026, 6, 1)  # Monday, confirmed XTAI session


def test_cancel_before_resolution_reflected_in_sessions_own_account() -> None:
    """No strategy path in Phase 5 ever calls broker.cancel() -- a cancel
    is a user action, not a signal. This drives session.broker directly
    (bypassing session.run(), since no strategy signal would produce a
    cancel) to confirm the session's OWN account and event_log -- a
    SEPARATE object from BrokerSimulator's internal mirror, both folding
    the same event stream -- correctly absorb a cancel that nothing else
    in the pipeline exercises."""
    session = PaperSession(
        strategy=BuyAndHold(),
        tickers=["2330"],
        feed=FixtureFeed(batches=[]),
        capital=Decimal("1000000"),
        cost_model=TaiwanCostModel(),
        fill_model=FullFill(),
        settlement=NettingSettlement(),
        max_position_pct=1.0,
    )
    order = Order(
        order_id=OrderId("o1"),
        ticker="2330",
        decided_at=_T1,
        side=Side.BUY,
        quantity=1000,
        tif=TimeInForce.IOC,
    )
    session.broker.submit(order)
    session.broker.cancel(OrderId("o1"))

    events = session.broker.drain_events()
    for e in events:
        session.event_log.append(e)
    fold(events, into=session.account)

    assert [e.kind for e in events] == [EventKind.SUBMITTED, EventKind.CANCEL]

    # both mirrors agree: the broker's own query() AND the session's
    # independently-folded account
    assert session.broker.query(OrderId("o1")).status == OrderStatus.CANCELLED
    assert session.account._orders[OrderId("o1")].status == OrderStatus.CANCELLED
    assert (
        "2330" not in session.account.positions
        or session.account.positions["2330"].open_shares == 0
    )

    logged_kinds = [e.kind for e in session.event_log]
    assert logged_kinds == [EventKind.SUBMITTED, EventKind.CANCEL]
