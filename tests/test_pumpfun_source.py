import json

from memecoin_trader.config import PumpFunSignalConfig
from memecoin_trader.signals.pumpfun_source import PumpFunLaunchSource

CONFIG = PumpFunSignalConfig(enabled=True, min_age_minutes=8, max_buffer_minutes=120)

MINT_A = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"
MINT_B = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"


def make_source(monkeypatch):
    source = PumpFunLaunchSource(config=CONFIG, chain_id="solana")
    monkeypatch.setattr(source, "_ensure_connected", lambda: None)
    return source


def test_message_without_mint_is_ignored(monkeypatch):
    source = make_source(monkeypatch)
    source._handle_message(json.dumps({"symbol": "MOON"}))
    assert source.poll() == []


def test_malformed_message_does_not_raise(monkeypatch):
    source = make_source(monkeypatch)
    source._handle_message("not json")
    assert source.poll() == []


def test_fresh_launch_is_buffered_not_emitted_immediately(monkeypatch):
    source = make_source(monkeypatch)
    source._handle_message(json.dumps({"mint": MINT_A, "symbol": "MOON", "name": "Moon Coin"}))

    assert source.poll() == []  # too young, still buffered
    assert MINT_A in source._buffer


def test_launch_emitted_once_it_clears_min_age(monkeypatch):
    source = make_source(monkeypatch)
    source._handle_message(json.dumps({"mint": MINT_A, "symbol": "MOON", "name": "Moon Coin"}))
    source._buffer[MINT_A]["first_seen"] -= CONFIG.min_age_minutes * 60 + 1

    signals = source.poll()

    assert len(signals) == 1
    assert signals[0].token_address == MINT_A
    assert signals[0].symbol == "MOON"
    assert signals[0].source == "pumpfun_launch"
    assert MINT_A not in source._buffer  # emitted, removed so it isn't re-emitted

    assert source.poll() == []  # already emitted once, won't re-emit


def test_launch_never_clearing_bar_is_dropped_after_max_buffer(monkeypatch):
    source = make_source(monkeypatch)
    source._handle_message(json.dumps({"mint": MINT_A, "symbol": "MOON"}))
    source._buffer[MINT_A]["first_seen"] -= CONFIG.max_buffer_minutes * 60 + 1

    assert source.poll() == []
    assert MINT_A not in source._buffer  # given up on, not emitted


def test_duplicate_mint_messages_do_not_reset_first_seen(monkeypatch):
    source = make_source(monkeypatch)
    source._handle_message(json.dumps({"mint": MINT_A, "symbol": "MOON"}))
    original_first_seen = source._buffer[MINT_A]["first_seen"]

    source._handle_message(json.dumps({"mint": MINT_A, "symbol": "MOON"}))

    assert source._buffer[MINT_A]["first_seen"] == original_first_seen


def test_multiple_launches_tracked_independently(monkeypatch):
    source = make_source(monkeypatch)
    source._handle_message(json.dumps({"mint": MINT_A, "symbol": "MOON"}))
    source._handle_message(json.dumps({"mint": MINT_B, "symbol": "DOGE"}))
    for mint in (MINT_A, MINT_B):
        source._buffer[mint]["first_seen"] -= CONFIG.min_age_minutes * 60 + 1

    signals = source.poll()

    assert {s.token_address for s in signals} == {MINT_A, MINT_B}
