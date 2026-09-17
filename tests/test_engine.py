"""End-to-end test of one full buy-then-exit cycle through the real engine
wiring (ledger + strategies + paper executor), with only the two outside
inputs (Twitter signal, DexScreener market data) replaced by fakes."""
from decimal import Decimal

from memecoin_trader.config import load_settings
from memecoin_trader.engine import TradingEngine
from memecoin_trader.execution.paper_executor import PaperExecutor
from tests.conftest import make_pair, make_signal


class FakeSignalSource:
    def __init__(self):
        self.queue = []

    def poll(self):
        out, self.queue = self.queue, []
        return out


class FakeMarket:
    def __init__(self):
        self.pairs = {}

    def get_best_pair_for_token(self, chain_id, token_address):
        return self.pairs.get(token_address)


def build_engine(conn):
    settings = load_settings()
    signal_source = FakeSignalSource()
    market = FakeMarket()
    executor = PaperExecutor(settings.paper_execution)
    engine = TradingEngine(settings, conn, market, signal_source, executor)
    return engine, signal_source, market


def test_full_cycle_buy_then_stop_loss_sell(conn):
    engine, signal_source, market = build_engine(conn)
    token = "TOKEN1111111111111111111111111111111111111"

    market.pairs[token] = make_pair(token_address=token, price_usd="1.0")
    signal_source.queue = [make_signal(token_address=token, score=90)]

    engine._poll_signals()

    positions = engine.ledger.get_open_positions()
    assert len(positions) == 1
    assert engine.ledger.get_cash_usd() < Decimal("100")

    # price crashes 30% -> stop loss should fire
    market.pairs[token] = make_pair(token_address=token, price_usd="0.70")
    engine._manage_open_positions()

    assert engine.ledger.get_open_positions() == []
    trades = engine.ledger.get_recent_trades()
    sell_trades = [t for t in trades if t.side == "sell"]
    assert len(sell_trades) == 1
    assert sell_trades[0].reason == "stop_loss"
    assert sell_trades[0].realized_pnl_usd < 0

    engine._record_equity()
    curve = engine.ledger.get_equity_curve()
    assert len(curve) == 1
    assert curve[0]["equity_usd"] < 100.0  # lost money net of fees+slippage, as expected


def test_low_quality_signal_is_rejected_and_logged(conn):
    engine, signal_source, market = build_engine(conn)
    token = "TOKEN2222222222222222222222222222222222222"

    market.pairs[token] = make_pair(token_address=token, price_usd="1.0", liquidity_usd="100")
    signal_source.queue = [make_signal(token_address=token, score=90)]  # good score, bad liquidity

    engine._poll_signals()

    assert engine.ledger.get_open_positions() == []
    assert engine.ledger.get_cash_usd() == Decimal("100")
    signals_logged = engine.ledger.get_recent_signals()
    assert len(signals_logged) == 1
    assert signals_logged[0]["acted_on"] == 0
