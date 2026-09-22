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


def test_position_detail_404s_for_unknown_id(client):
    c, _ = client
    resp = c.get("/api/position/999999")
    assert resp.status_code == 404


def test_position_detail_returns_open_position_with_trades(client):
    c, db_path = client
    token = "TOKEN4444444444444444444444444444444444444"
    _seed_open_position(db_path, token)

    conn = get_connection(db_path)
    position = Ledger(conn).get_open_position_for_token(token)

    resp = c.get(f"/api/position/{position.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == position.id
    assert data["token_address"] == token
    assert data["status"] == "open"
    assert len(data["trades"]) == 1
    assert data["trades"][0]["side"] == "buy"


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
    assert c.post("/api/big-risk/start").status_code == 401
    assert c.post("/api/big-risk/cancel").status_code == 401
    assert c.post("/api/big-risk/stop").status_code == 401
    assert c.get("/", auth=("alice", "wrong")).status_code == 401
    assert c.get("/", auth=("alice", "s3cret")).status_code == 200
    assert c.post("/api/online", auth=("alice", "s3cret")).status_code == 200


def test_big_risk_start_with_no_open_positions(client):
    c, db_path = client
    resp = c.post("/api/big-risk/start")
    assert resp.status_code == 200
    data = resp.json()
    assert data == {
        "started": True, "mode": "searching", "sold": 0,
        "message": "BIG RISK ARMED. Sold 0 position(s) — scanning for a target...",
    }

    conn = get_connection(db_path)
    assert Ledger(conn).get_big_risk_state().mode == "searching"


def test_big_risk_start_sells_existing_positions_first(client, monkeypatch):
    c, db_path = client
    token = "TOKENBIGRISK111111111111111111111111111111"
    _seed_open_position(db_path, token)
    monkeypatch.setattr(
        DexScreenerClient, "get_best_pair_for_token", lambda self, chain_id, addr: make_pair(token_address=addr)
    )

    resp = c.post("/api/big-risk/start")
    data = resp.json()
    assert data["started"] is True
    assert data["sold"] == 1

    conn = get_connection(db_path)
    assert Ledger(conn).get_open_positions() == []


def test_big_risk_start_is_a_noop_while_already_active(client):
    c, _ = client
    c.post("/api/big-risk/start")
    resp = c.post("/api/big-risk/start")
    data = resp.json()
    assert data == {"started": False, "mode": "searching", "message": "Big Risk is already active."}


def test_big_risk_cancel_while_searching(client):
    c, db_path = client
    c.post("/api/big-risk/start")
    resp = c.post("/api/big-risk/cancel")
    assert resp.json() == {"cancelled": True, "mode": "idle", "message": "Big Risk search cancelled."}

    conn = get_connection(db_path)
    assert Ledger(conn).get_big_risk_state().mode == "idle"


def test_big_risk_cancel_with_nothing_to_cancel(client):
    c, _ = client
    resp = c.post("/api/big-risk/cancel")
    assert resp.json() == {"cancelled": False, "mode": "idle", "message": "No Big Risk search in progress."}


def test_big_risk_stop_with_no_position(client):
    c, _ = client
    resp = c.post("/api/big-risk/stop")
    assert resp.json() == {"sold": False, "message": "No Big Risk position to sell."}


def test_big_risk_stop_sells_the_invested_position(client, monkeypatch):
    c, db_path = client
    token = "TOKENBIGRISK222222222222222222222222222222"
    _seed_open_position(db_path, token)
    conn = get_connection(db_path)
    ledger = Ledger(conn)
    position = ledger.get_open_position_for_token(token)
    ledger.set_big_risk_invested(position.id)
    conn.close()

    monkeypatch.setattr(
        DexScreenerClient, "get_best_pair_for_token", lambda self, chain_id, addr: make_pair(token_address=addr)
    )

    resp = c.post("/api/big-risk/stop")
    assert resp.json() == {"sold": True, "message": "Sold."}

    conn = get_connection(db_path)
    assert Ledger(conn).get_open_positions() == []
    assert Ledger(conn).get_big_risk_state().mode == "idle"


def test_api_summary_includes_big_risk_state(client):
    c, _ = client
    resp = c.get("/api/summary")
    data = resp.json()
    assert data["big_risk"]["mode"] == "idle"
    assert data["big_risk"]["position"] is None
    assert data["big_risk"]["search_window_seconds"] > 0


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


def test_index_renders_equity_range_buttons_defaulting_to_all(client):
    c, _ = client
    resp = c.get("/")
    for r in ["1MIN", "5M", "1H", "1D", "1W", "1M", "YTD", "ALL"]:
        assert f">{r}<" in resp.text
    assert 'data-range="all"' in resp.text


def test_mascot_dances_when_flat_or_in_the_green(client):
    c, _ = client
    resp = c.get("/")
    assert 'data-mode="dance"' in resp.text
    assert resp.text.count('class="mascot-canvas"') == 3  # a little crew, not a lone guy


def test_index_renders_the_club(client):
    c, _ = client
    resp = c.get("/")
    assert 'id="clubCanvas"' in resp.text


def test_mascot_is_sad_when_in_the_red(client):
    c, db_path = client
    conn = get_connection(db_path)
    conn.execute("UPDATE portfolio_state SET cash_usd = '50' WHERE id = 1")
    conn.commit()
    conn.close()

    resp = c.get("/")
    assert 'data-mode="sad"' in resp.text


def _seed_equity_history(db_path):
    from datetime import datetime, timedelta, timezone

    conn = get_connection(db_path)
    now = datetime.now(timezone.utc)
    conn.execute(
        "INSERT INTO equity_history (cash_usd, positions_value_usd, equity_usd, recorded_at) VALUES (?, ?, ?, ?)",
        ("90", "0", "90", (now - timedelta(days=2)).isoformat()),
    )
    conn.execute(
        "INSERT INTO equity_history (cash_usd, positions_value_usd, equity_usd, recorded_at) VALUES (?, ?, ?, ?)",
        ("110", "0", "110", (now - timedelta(minutes=1)).isoformat()),
    )
    conn.commit()
    conn.close()


def test_api_summary_range_param_filters_the_equity_curve(client):
    c, db_path = client
    _seed_equity_history(db_path)

    resp = c.get("/api/summary?range=1d")
    data = resp.json()
    assert data["equity_range"] == "1d"
    assert [p["equity_usd"] for p in data["equity_curve"]] == [110.0]

    resp = c.get("/api/summary?range=all")
    data = resp.json()
    assert [p["equity_usd"] for p in data["equity_curve"]] == [90.0, 110.0]


def test_api_summary_unknown_range_falls_back_to_all(client):
    c, db_path = client
    _seed_equity_history(db_path)

    resp = c.get("/api/summary?range=nonsense")
    data = resp.json()
    assert data["equity_range"] == "all"
    assert len(data["equity_curve"]) == 2
