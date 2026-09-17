from unittest.mock import MagicMock

import requests

from memecoin_trader.market.rugcheck import RugCheckClient


def make_session(json_data=None, status_code=200, raise_exc=None):
    session = MagicMock()
    resp = MagicMock()
    resp.status_code = status_code
    if raise_exc:
        session.get.side_effect = raise_exc
    else:
        resp.raise_for_status = MagicMock()
        resp.json.return_value = json_data
        session.get.return_value = resp
    return session


def test_parses_danger_and_warning_flags():
    session = make_session(
        {
            "score": 42,
            "risks": [
                {"name": "Mint authority not renounced", "level": "danger"},
                {"name": "Low liquidity", "level": "warn"},
            ],
            "token": {"mintAuthority": "somekey", "freezeAuthority": None},
            "markets": [{"lp": {"lpLockedPct": 80.0}}, {"lp": {"lpLockedPct": 60.0}}],
        }
    )
    client = RugCheckClient(session=session)
    report = client.get_risk_report("TOKEN1")

    assert report is not None
    assert report.score == 42.0
    assert report.danger_flags == ("Mint authority not renounced",)
    assert report.warning_flags == ("Low liquidity",)
    assert report.is_high_risk is True
    assert report.mint_authority_renounced is False
    assert report.freeze_authority_renounced is True
    assert report.lp_locked_pct == 60.0  # worst case across pools


def test_clean_report_has_no_flags_and_is_not_high_risk():
    session = make_session({"score": 5, "risks": [], "token": {}, "markets": []})
    client = RugCheckClient(session=session)
    report = client.get_risk_report("TOKEN1")

    assert report is not None
    assert report.danger_flags == ()
    assert report.is_high_risk is False
    assert report.lp_locked_pct is None


def test_404_returns_none_not_an_error():
    session = make_session(status_code=404)
    client = RugCheckClient(session=session)
    assert client.get_risk_report("TOKEN1") is None


def test_network_failure_returns_none():
    session = make_session(raise_exc=requests.ConnectionError("boom"))
    client = RugCheckClient(session=session)
    assert client.get_risk_report("TOKEN1") is None


def test_malformed_response_returns_none_instead_of_crashing():
    session = make_session({"risks": "not-a-list-surprise"})
    client = RugCheckClient(session=session)
    assert client.get_risk_report("TOKEN1") is None
