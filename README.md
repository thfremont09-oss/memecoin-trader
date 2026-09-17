# Memecoin Trader

A simulation-first bot that watches for social-media FOMO around Solana
memecoins, screens candidates against real market data, and paper-trades
them starting from **$100**. It's built so that going from simulation to
real trading later is a config change, not a rewrite.

## How it works

```
signal source  ──▶  entry strategy  ──▶  paper/live executor  ──▶  ledger (SQLite)
(Twitter/mock)       (filters:            (fills against real         │
     │                liquidity,           DexScreener prices,        ▼
     ▼                age, cooldown,       with slippage + fees)   dashboard
real DexScreener      max positions)                                (web UI)
market data                                                            ▲
     │                                                                 │
     └────────────▶  exit strategy  ──▶ paper/live executor ──────────┘
                     (stop loss, take
                      profit, trailing
                      stop, time exit,
                      liquidity-rug exit)
```

**What's real vs. simulated right now:**

| Piece | Status |
|---|---|
| Market data (price, liquidity, volume, pair age) | **Real** — pulled live from the public [DexScreener API](https://docs.dexscreener.com/api/reference), no key needed |
| Trade fills (slippage, fees) | **Simulated**, but modeled on the actual liquidity of the real pair, so a thin pool gets realistically worse fills than a deep one |
| Twitter/X hype signal | **Simulated** by default — see below. A real X API adapter exists and is a one-line switch away once you have API access |
| Money | **Simulated** ("paper" mode) by default. A real Solana execution path exists (`--live`) but is off by default and hard-gated — see [Going live](#going-live) |

### Why the Twitter signal is simulated

Real-time tweet search requires a paid X API tier. Since you don't have
credentials configured, `MockTwitterSource` stands in for it: it pulls
**real** tokens that are currently being promoted on DexScreener (the same
kind of freshly-launched, actively-hyped tokens that generate organic
Twitter chatter) and synthesizes plausible "mention spike" events on top of
them — clearly labeled `[SIMULATED]` in logs and the dashboard so nothing is
mistaken for a real tweet.

The market side of every simulated trade is 100% real data. Only the "why
did we buy this" trigger is synthetic. When you get X API access, drop your
bearer token into `.env` and the bot automatically switches to
`TwitterAPISource` (`memecoin_trader/signals/twitter_source.py`) — same
interface, same downstream code, real tweets in, real token addresses and
cashtags extracted, engagement-weighted scoring.

### Why the simulation should be trusted

- All money math uses Python `Decimal` and is stored as exact decimal
  strings in SQLite — never floats — so nothing drifts from rounding.
- Every buy/sell is filled against the **current real price** of the real
  pair, not an idealized number.
- Slippage scales with trade size relative to real pool liquidity (a $40
  buy into a $5,000 pool moves the fill price a lot more than the same $40
  into a $500,000 pool), plus a simulated DEX/network fee — both configurable
  in `config.yaml`.
- Entry filters mirror real risk controls a careful trader would use:
  minimum liquidity/volume, minimum pair age (avoids instant-rug sniper
  windows), a cooldown before re-buying a token you just sold, and a cap on
  concurrent positions.
- Exit strategy isn't just "sell at +X%": it does partial take-profit
  (bank half the position, let the rest run), a trailing stop off the peak
  price, a hard stop-loss, a liquidity-rug emergency exit if the pool
  drains, and a time-based exit so nothing gets held forever.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # optional — works fine with everything blank
```

## Usage

```bash
# Start the trading loop (paper mode, $100 starting balance)
python -m memecoin_trader.cli run

# In another terminal: check balance / positions / P&L
python -m memecoin_trader.cli status

# Or launch the web dashboard (auto-refreshing balance, equity chart,
# open positions, trade history) at http://127.0.0.1:8787
python -m memecoin_trader.cli dashboard

# Wipe the simulation and start over from $100
python -m memecoin_trader.cli reset
```

(If you installed the package with `pip install -e .`, you can also just
run `memecoin-trader run` / `status` / `dashboard` / `reset`.)

All state lives in `data/trader.db` (SQLite) and `data/trader.log`. Both are
gitignored.

## Configuring the strategy

Everything that isn't a secret lives in `config.yaml`: how often it polls,
entry thresholds (minimum liquidity/volume, hype score, position sizing),
exit thresholds (stop-loss %, take-profit %, trailing stop %, max hold
time), and the simulated slippage/fee model. Secrets (API keys, wallet key)
go in `.env`, never in `config.yaml`.

## Going live

Real trading is implemented (`memecoin_trader/execution/live_executor.py`,
using the [Jupiter](https://station.jup.ag/docs/apis/swap-api) aggregator on
Solana) but is **experimental and has not been exercised against mainnet**.
Treat it as a starting point to test carefully, not a finished product.

It refuses to run unless **all** of these hold:

1. `mode: live` in `config.yaml` (or `MODE=live` in `.env`, or `--live` on
   the CLI)
2. `I_UNDERSTAND_LIVE_TRADING_RISK=yes` in `.env`
3. `SOLANA_PRIVATE_KEY` set in `.env` (base58-encoded secret key — **use a
   dedicated wallet with a small amount of SOL, never your main wallet**)
4. `pip install -r requirements-live.txt` (installs `solders` + `base58`,
   not required for paper trading)

Even then, every individual trade is capped at `execution.live.max_trade_usd`
in `config.yaml` (default $5) as a hard safety limit, independent of your
strategy's position sizing.

**Before risking real money:**

- Test with a fresh wallet funded with only a few dollars of SOL.
- Note the `LiveExecutor.sell()` decimals caveat in the code — it currently
  assumes 6-decimal SPL tokens (true for most memecoins, not guaranteed).
  Verify the mint's actual decimals before trusting a live sell size.
- Watch the first several trades closely via `status`/`dashboard` and the
  Solana transaction signatures logged for each fill.
- Get real Twitter/X API access too — trading real money on the simulated
  hype feed's synthetic signals would just be gambling on whichever tokens
  happen to be boosted on DexScreener that day.

## Running the tests

```bash
pip install -r requirements.txt
pytest
```

Tests cover the ledger's money math, entry/exit strategy rules, the paper
executor's slippage/fee model, technical indicators, and one full
buy-then-exit cycle through the real engine wiring.

## Project layout

```
memecoin_trader/
  config.py              settings loaded from config.yaml + .env
  engine.py              the main loop tying everything together
  cli.py                 run / status / dashboard / reset commands
  reporting.py           shared summary builder (CLI status + dashboard)
  signals/
    base.py              SignalSource interface, SocialSignal
    mock_source.py        simulated hype feed over real trending tokens
    twitter_source.py     real X API v2 adapter (needs TWITTER_BEARER_TOKEN)
  market/
    dexscreener.py        real public market data client
  analysis/
    indicators.py          SMA, momentum, drawdown, RSI over collected price history
    entry_strategy.py       buy filters
    exit_strategy.py        stop-loss / take-profit / trailing-stop / time / rug exits
  portfolio/
    db.py / models.py / ledger.py   SQLite-backed, Decimal-accurate accounting
  execution/
    base.py               Executor interface, FillResult
    paper_executor.py      simulated fills with realistic slippage
    live_executor.py       real Solana swaps via Jupiter (gated, experimental)
  dashboard/
    server.py + templates/index.html   local FastAPI dashboard
tests/                    pytest suite
```
