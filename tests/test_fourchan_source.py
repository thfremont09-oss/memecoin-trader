from unittest.mock import MagicMock

import requests

from memecoin_trader.config import FourChanSignalConfig
from memecoin_trader.signals.fourchan_source import MAX_SIGNAL_SCORE, FourChanBizSource

CONFIG = FourChanSignalConfig(enabled=True, boards=["biz"], max_threads_per_poll=40, mention_cooldown_minutes=30)

SOLANA_ADDR_A = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"
SOLANA_ADDR_B = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"


def make_session(pages=None, raise_exc=None):
    session = MagicMock()
    if raise_exc:
        session.get.side_effect = raise_exc
    else:
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = pages
        session.get.return_value = resp
    return session


def make_catalog(threads):
    return [{"page": 1, "threads": threads}]


def test_poll_extracts_addresses_and_strips_html():
    pages = make_catalog(
        [
            {"no": 1, "sub": "gem alert", "com": f"$MOON is pumping<br>CA: {SOLANA_ADDR_A}"},
            {"no": 2, "com": f"different coin {SOLANA_ADDR_B}"},
        ]
    )
    session = make_session(pages)
    source = FourChanBizSource(config=CONFIG, chain_id="solana", session=session)

    signals = source.poll()

    by_token = {s.token_address: s for s in signals}
    assert set(by_token) == {SOLANA_ADDR_A, SOLANA_ADDR_B}
    assert "<br>" not in by_token[SOLANA_ADDR_A].excerpt
    assert all(s.source == "fourchan_biz" for s in signals)


def test_score_never_exceeds_cap_regardless_of_mention_count():
    threads = [{"no": i, "com": f"CA {SOLANA_ADDR_A}"} for i in range(50)]
    session = make_session(make_catalog(threads))
    source = FourChanBizSource(config=CONFIG, chain_id="solana", session=session)

    signals = source.poll()

    assert len(signals) == 1
    assert signals[0].score <= MAX_SIGNAL_SCORE


def test_capped_score_stays_below_a_realistic_threshold_even_with_corroboration_bonus():
    # This is the actual safety property the module promises: 4chan alone
    # (even at its max score, even after the corroboration bonus) must
    # never clear a sane entry.mention_score_threshold on its own.
    typical_bonus = 15.0
    typical_threshold = 45.0
    assert MAX_SIGNAL_SCORE + typical_bonus < typical_threshold


def test_request_failure_for_one_board_does_not_raise():
    session = make_session(raise_exc=requests.RequestException("boom"))
    source = FourChanBizSource(config=CONFIG, chain_id="solana", session=session)
    assert source.poll() == []


def test_no_threads_returns_no_signals():
    session = make_session(make_catalog([]))
    source = FourChanBizSource(config=CONFIG, chain_id="solana", session=session)
    assert source.poll() == []


def test_respects_max_threads_per_poll():
    config = FourChanSignalConfig(enabled=True, boards=["biz"], max_threads_per_poll=1, mention_cooldown_minutes=30)
    threads = [
        {"no": 1, "com": f"CA {SOLANA_ADDR_A}"},
        {"no": 2, "com": f"CA {SOLANA_ADDR_B}"},
    ]
    session = make_session(make_catalog(threads))
    source = FourChanBizSource(config=config, chain_id="solana", session=session)

    signals = source.poll()

    assert len(signals) == 1  # only the first thread was considered
