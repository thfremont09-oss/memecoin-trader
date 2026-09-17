"""End-to-end test of one full buy-then-exit cycle through the real engine
wiring (ledger + strategies + paper executor), with only the two outside
inputs (Twitter signal, DexScreener market data) replaced by fakes."""
from decimal import Decimal

from memecoin_trader.config import load_settings
from memecoin_trader.engine import TradingEngine
from memecoin_trader.execution.paper_executor import PaperExecutor
from tests.conftest import make_pair, make_rug_report, make_signal


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


class FakeRugCheckClient:
    """Stands in for the real (network-calling) RugCheckClient in tests —
    reports every token as clean unless a test explicitly overrides it."""

    def __init__(self):
        self.reports = {}

    def get_risk_report(self, token_address):
        if token_address in self.reports:
            return self.reports[token_address]
        return make_rug_report(token_address=token_address)


def build_engine(conn):
    settings = load_settings()
    signal_source = FakeSignalSource()
    market = FakeMarket()
    executor = PaperExecutor(settings.paper_execution)
    rug_client = FakeRugCheckClient()
    engine = TradingEngine(settings, conn, market, signal_source, executor, rug_client=rug_client)
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


def test_dangerous_rug_report_blocks_the_buy(conn):
    engine, signal_source, market = build_engine(conn)
    token = "TOKEN3333333333333333333333333333333333333"

    market.pairs[token] = make_pair(token_address=token, price_usd="1.0")
    engine.rug_client.reports[token] = make_rug_report(
        token_address=token, danger_flags=("Mint authority not renounced",)
    )
    signal_source.queue = [make_signal(token_address=token, score=90)]

    engine._poll_signals()

    assert engine.ledger.get_open_positions() == []
    assert engine.ledger.get_cash_usd() == Decimal("100")


def test_successful_buy_stores_trade_features_for_training(conn):
    engine, signal_source, market = build_engine(conn)
    token = "TOKEN4444444444444444444444444444444444444"

    market.pairs[token] = make_pair(token_address=token, price_usd="1.0")
    signal_source.queue = [make_signal(token_address=token, score=90)]
    engine._poll_signals()

    position = engine.ledger.get_open_positions()[0]

    # close it out so it shows up in the training dataset
    market.pairs[token] = make_pair(token_address=token, price_usd="0.70")
    engine._manage_open_positions()

    dataset = engine.ledger.get_training_dataset()
    assert len(dataset) == 1
    features, label = dataset[0]
    assert label == 0  # closed at a loss
    assert features["signal_score"] == 90.0


def test_liquidate_all_sells_every_open_position(conn):
    engine, signal_source, market = build_engine(conn)
    token_a = "TOKEN5555555555555555555555555555555555555"
    token_b = "TOKEN6666666666666666666666666666666666666"

    for token in (token_a, token_b):
        market.pairs[token] = make_pair(token_address=token, price_usd="1.0")
        signal_source.queue = [make_signal(token_address=token, score=90)]
        engine._poll_signals()

    assert len(engine.ledger.get_open_positions()) == 2
    cash_before = engine.ledger.get_cash_usd()

    closed = engine.liquidate_all()

    assert closed == 2
    assert engine.ledger.get_open_positions() == []
    assert engine.ledger.get_cash_usd() > cash_before  # proceeds landed back in cash
    trades = [t for t in engine.ledger.get_recent_trades() if t.side == "sell"]
    assert all(t.reason == "manual_liquidation" for t in trades)


def test_liquidate_all_skips_positions_with_no_market_data(conn):
    engine, signal_source, market = build_engine(conn)
    token = "TOKEN7777777777777777777777777777777777777"

    market.pairs[token] = make_pair(token_address=token, price_usd="1.0")
    signal_source.queue = [make_signal(token_address=token, score=90)]
    engine._poll_signals()

    del market.pairs[token]  # simulate no market data available (e.g. delisted, network issue)

    closed = engine.liquidate_all()

    assert closed == 0
    assert len(engine.ledger.get_open_positions()) == 1  # left open, not lost


def test_liquidate_all_is_a_noop_with_no_open_positions(conn):
    engine, signal_source, market = build_engine(conn)
    assert engine.liquidate_all() == 0
