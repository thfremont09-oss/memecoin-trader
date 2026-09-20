from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

import memecoin_trader.dashboard.server as server
import memecoin_trader.engine as engine_module
from memecoin_trader.execution.base import FillResult
from memecoin_trader.market.dexscreener import DexScreenerClient
from memecoin_trader.portfolio.db import get_connection, init_db
from memecoin_trader.portfolio.ledger import Ledger
from tests.conftest import make_pair, make_signal


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "dashboard_test.db"
    monkeypatch.setattr(server, "DB_PATH", db_path)
    monkeypatch.setattr(engine_module, "DB_PATH", db_path)
    conn = get_connection(db_path)
    init_db(conn, Decimal("100"))
    conn.close()
    return TestClient(server.app), db_path


def test_index_renders_with_offline_online_buttons(client):
    c, _ = client
    resp = c.get("/")
    assert resp.status_code == 200
    assert "Go offline (sell all)" in resp.text
    assert "Go online" in resp.text
    assert "online" in resp.text.lower()  # status badge


def test_api_summary_returns_starting_state(client):
    c, _ = client
    resp = c.get("/api/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["equity_usd"] == 100.0
    assert data["open_positions"] == []
    assert data["trading_enabled"] is True


def test_offline_with_no_open_positions_still_disables_trading(client):
    c, db_path = client
    resp = c.post("/api/offline")
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"trading_enabled": False, "closed": 0, "total": 0, "message": "Offline. No open positions to sell."}

    conn = get_connection(db_path)
    assert Ledger(conn).is_trading_enabled() is False


def test_online_reenables_trading(client):
    c, db_path = client
    c.post("/api/offline")
    resp = c.post("/api/online")
    assert resp.status_code == 200
    assert resp.json()["trading_enabled"] is True

    conn = get_connection(db_path)
    assert Ledger(conn).is_trading_enabled() is True


def test_sell_one_with_no_such_position(client):
    c, _ = client
    resp = c.post("/api/sell/NOT_A_REAL_TOKEN")
    assert resp.status_code == 200
    assert resp.json() == {"sold": False, "message": "That position isn't open anymore."}


def _seed_open_position(db_path, token_address):
    conn = get_connection(db_path)
    ledger = Ledger(conn)
    fill = FillResult(price_usd=Decimal("1.0"), quantity=Decimal("10"), amount_usd=Decimal("10"), fee_usd=Decimal("0.1"), tx_id=None)
    ledger.open_position(
        token_address=token_address,
        symbol="MEME",
        chain_id="solana",
        fill=fill,
        signal=make_signal(token_address=token_address),
        entry_liquidity_usd=Decimal("20000"),
        mode="paper",
    )
    conn.close()


def test_offline_sells_a_real_open_position(client, monkeypatch):
    c, db_path = client
    token = "TOKEN1111111111111111111111111111111111111"
    _seed_open_position(db_path, token)

    monkeypatch.setattr(
        DexScreenerClient, "get_best_pair_for_token", lambda self, chain_id, addr: make_pair(token_address=addr)
    )

    resp = c.post("/api/offline")
    data = resp.json()
    assert data["trading_enabled"] is False
    assert data["closed"] == 1
    assert data["total"] == 1

    conn = get_connection(db_path)
    assert Ledger(conn).get_open_positions() == []


def test_sell_one_sells_only_that_position(client, monkeypatch):
    c, db_path = client
    token_a = "TOKEN2222222222222222222222222222222222222"
    token_b = "TOKEN3333333333333333333333333333333333333"
    _seed_open_position(db_path, token_a)
    _seed_open_position(db_path, token_b)

    monkeypatch.setattr(
        DexScreenerClient, "get_best_pair_for_token", lambda self, chain_id, addr: make_pair(token_address=addr)
    )

    resp = c.post(f"/api/sell/{token_a}")
    assert resp.json() == {"sold": True, "message": "Sold."}

    conn = get_connection(db_path)
    remaining = Ledger(conn).get_open_positions()
    assert len(remaining) == 1
    assert remaining[0].token_address == token_b


def test_dashboard_requires_auth_when_credentials_configured(client, monkeypatch):
    c, _ = client
    monkeypatch.setenv("DASHBOARD_USERNAME", "alice")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "s3cret")

    assert c.get("/").status_code == 401
    assert c.post("/api/offline").status_code == 401
    assert c.post("/api/online").status_code == 401
    assert c.post("/api/sell/TOKEN1").status_code == 401
    assert c.post("/api/caution-level/4").status_code == 401
    assert c.get("/", auth=("alice", "wrong")).status_code == 401
    assert c.get("/", auth=("alice", "s3cret")).status_code == 200
    assert c.post("/api/online", auth=("alice", "s3cret")).status_code == 200


def test_index_renders_caution_slider_at_default_level(client):
    c, _ = client
    resp = c.get("/")
    assert 'id="cautionSlider"' in resp.text
    assert 'value="3"' in resp.text
    assert "Balanced" in resp.text


def test_set_caution_level(client):
    c, db_path = client
    resp = c.post("/api/caution-level/1")
    assert resp.status_code == 200
    assert resp.json() == {"caution_level": 1, "label": "Very cautious", "message": "Caution level set to 1 (Very cautious)."}

    conn = get_connection(db_path)
    assert Ledger(conn).get_caution_level() == 1


def test_set_caution_level_clamps_out_of_range_values(client):
    c, _ = client
    resp = c.post("/api/caution-level/99")
    assert resp.status_code == 200
    assert resp.json()["caution_level"] == 5

    resp = c.post("/api/caution-level/0")
    assert resp.json()["caution_level"] == 1


def test_summary_reflects_current_caution_level(client):
    c, _ = client
    c.post("/api/caution-level/5")
    resp = c.get("/api/summary")
    data = resp.json()
    assert data["caution_level"] == 5
    assert data["caution_label"] == "Aggressive"
