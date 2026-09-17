from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

import memecoin_trader.dashboard.server as server
import memecoin_trader.engine as engine_module
from memecoin_trader.portfolio.db import get_connection, init_db


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "dashboard_test.db"
    monkeypatch.setattr(server, "DB_PATH", db_path)
    monkeypatch.setattr(engine_module, "DB_PATH", db_path)
    conn = get_connection(db_path)
    init_db(conn, Decimal("100"))
    conn.close()
    return TestClient(server.app)


def test_index_renders_with_sell_all_button(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Sell everything now" in resp.text
    assert "Memecoin Trader" in resp.text


def test_api_summary_returns_starting_state(client):
    resp = client.get("/api/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["equity_usd"] == 100.0
    assert data["open_positions"] == []


def test_liquidate_with_no_open_positions(client):
    resp = client.post("/api/liquidate")
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"closed": 0, "total": 0, "message": "No open positions."}


def test_dashboard_requires_auth_when_credentials_configured(client, monkeypatch):
    monkeypatch.setenv("DASHBOARD_USERNAME", "alice")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "s3cret")

    assert client.get("/").status_code == 401
    assert client.post("/api/liquidate").status_code == 401
    assert client.get("/", auth=("alice", "wrong")).status_code == 401
    assert client.get("/", auth=("alice", "s3cret")).status_code == 200
    assert client.post("/api/liquidate", auth=("alice", "s3cret")).status_code == 200
