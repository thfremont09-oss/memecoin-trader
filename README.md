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
| Rug-pull screening | **Real** — [RugCheck.xyz](https://rugcheck.xyz) checked before every buy (mint/freeze authority, LP lock, holder risk), plus liquidity/FDV and price-spike heuristics from DexScreener data |
| Trade-quality ML model | **Off by default** — trains on the bot's own closed-trade history once there's enough of it; see [Machine learning](#machine-learning) |
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
  price, a liquidity-rug emergency exit if the pool drains (both a slower
  cumulative-decline check and a fast single-interval-drop check), a hard
  stop-loss, and a time-based exit so nothing gets held forever.

## Rug-pull defenses

Every candidate token that clears the basic hype-score bar goes through
extra screening before a buy, all configurable under `entry:` in
`config.yaml`:

- **[RugCheck.xyz](https://rugcheck.xyz)** (free, no key) is queried for the
  token's actual on-chain state — an un-renounced mint authority (deployer
  can print unlimited supply), an active freeze authority (deployer can
  freeze your wallet's tokens), unlocked LP tokens (deployer can pull
  liquidity instantly), and other named risk flags. Any "danger"-level
  finding blocks the trade by default (`entry.rug_check.max_danger_flags`).
- **Fails closed by default** (`entry.rug_check.fail_closed: true`): if the
  check errors out or the token isn't indexed yet, the trade is skipped
  rather than assumed safe. This means if RugCheck.xyz is ever unreachable,
  the bot will simply stop buying anything until it's reachable again —
  check the logs for `rug check unavailable` if entries seem to have
  stopped. Set `fail_closed: false` if you'd rather it trade through that.
- **Liquidity-to-FDV ratio** (`entry.min_liquidity_to_fdv_pct`): skips
  tokens whose liquidity is a razor-thin sliver of their reported
  valuation — an easy setup to manipulate or rug.
- **Price-spike cap** (`entry.max_price_change_5m_pct`): skips tokens that
  already spiked hard in the last 5 minutes, so the bot isn't chasing the
  top of a pump.
- **Sudden liquidity-drop exit** (`exit.sudden_liquidity_drop_pct`): for
  positions already held, an emergency exit fires if liquidity drops sharply
  between two consecutive checks — catching an in-progress rug faster than
  waiting for the cumulative decline-from-entry check to cross its floor.

This integration is unverified against RugCheck's live API from the sandbox
this was built in (its outbound network is restricted) — the parsing is
deliberately defensive, but watch the logs the first few times it runs for
real and tell me if anything about the response shape looks off.

## Machine learning

`memecoin_trader/ml/` adds a small logistic-regression model that predicts,
from a token's entry-time features (hype score, liquidity, volume, price
momentum, RugCheck score, etc.), the probability a trade will end up
profitable. Since a rug pull always shows up as a large loss, a model that
predicts plain profitability is implicitly learning to avoid rug-like
patterns too — there's no separate "is this a scam" label needed.

**It starts out as a no-op.** With zero trade history there's nothing to
learn from, so `entry.ml.enabled` defaults to `false` in `config.yaml`, and
even when enabled, the engine only uses it if a trained model file actually
exists — otherwise it's silently skipped. Every buy the bot makes
automatically saves its entry-time feature vector, so training data
accumulates on its own just from running the bot normally.

Once you've let it run long enough to close a decent number of trades:

```powershell
pip install -r requirements-ml.txt   # scikit-learn + joblib, not needed otherwise
python -m memecoin_trader.cli train
```

This reads every closed, feature-tagged position, labels it profitable (1)
or not (0) by its total realized P&L, trains a logistic regression model,
and saves it to `data/model.joblib`. It refuses to train with fewer than
`entry.ml.min_training_trades` closed trades (default 30) — there's no
point fitting a model to noise. Once you have a model you trust, set
`entry.ml.enabled: true`; the model's predicted confidence
(`entry.ml.min_confidence`) then becomes an additional gate on top of every
other filter above, not a replacement for any of them. Re-run `train`
periodically as more trade history accumulates.

## Setup (Windows PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env   # optional — works fine with everything blank
```

(The leading `.\` on the activate line matters — PowerShell won't run a
relative path without it and gives a confusing "module could not be loaded"
error instead.)

If `Activate.ps1` is blocked by your execution policy, run PowerShell as
Administrator once and allow local scripts:
```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

## Usage

```powershell
# Start the trading loop (paper mode, $100 starting balance)
python -m memecoin_trader.cli run

# In another terminal: check balance / positions / P&L
python -m memecoin_trader.cli status

# Or launch the web dashboard (auto-refreshing balance, equity chart,
# open positions, trade history) at http://127.0.0.1:8787
python -m memecoin_trader.cli dashboard

# Sell one position right now
python -m memecoin_trader.cli sell <token_address>

# Sell everything and stop the engine from opening new positions
python -m memecoin_trader.cli offline

# Let it start trading again
python -m memecoin_trader.cli online

# Sell everything right now, but keep looking for new trades (no offline switch)
python -m memecoin_trader.cli liquidate

# Wipe the simulation and start over from $100
python -m memecoin_trader.cli reset
```

(If you installed the package with `pip install -e .`, you can also just
run `memecoin-trader run` / `status` / `dashboard` / `sell` / `offline` /
`online` / `liquidate` / `reset`.)

**About `offline`/`online`:** these are the same actions as the dashboard's
"Go offline"/"Go online" buttons below. Going offline sells everything and
flips a switch the engine checks every cycle to stop opening new
positions — but it's a software switch, not a process kill: the engine
keeps running so it can still protect any position that couldn't be sold
(stop-loss, rug exits) and so `online` can flip it back. It is *not*
triggered automatically when your PC shuts down or the Scheduled Tasks
stop — that's a real Windows limitation, not a missing feature: stopping a
scheduled task (or shutting the PC down) kills the process outright, with
no reliable way to run code in that instant. Run `offline` yourself before
you know you're about to go properly offline (travel, etc.). It is also
never called by `update.ps1` or anything else automatically — only ever by
you, on purpose.

**On the dashboard** (top right): a status badge shows **online**/**offline**,
next to a **"Go offline (sell all)"** button and a **"Go online"** button —
whichever matches the current state is grayed out. Each row in the open
positions table also has its own **Sell** button, for closing out one
position without touching the rest or the online/offline switch.

All state lives in `data/trader.db` (SQLite) and `data/trader.log`. Both are
gitignored.

## Running 24/7 on your own Windows PC (free)

GitHub only holds the code — nothing runs there. The bot needs to run
somewhere that stays on. This is the free option: it uses the PC you already
own, needs no signup and no credit card, and doesn't change anything about
how the bot works — it just keeps `run` and `dashboard` running in the
background permanently, restarting them automatically if either ever
crashes, and starting them again automatically when you log in.

**Requirements:** the PC needs to actually stay on and logged in (it can be
locked, just not asleep or shut down) whenever you want the bot trading.

1. Finish the [Setup](#setup-windows-powershell) steps above first
   (`.venv` created, `pip install -r requirements.txt` done) if you haven't.

2. From inside the repo folder, run the installer once:
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\scripts\install_scheduled_tasks.ps1
   ```
   This registers two Windows Scheduled Tasks — `MemecoinTraderEngine` and
   `MemecoinTraderDashboard` — starts them immediately, and sets them to
   auto-start every time you log in and auto-restart within 5 seconds if
   either ever crashes.

3. Open **http://127.0.0.1:8787** any time to check balance/positions/trades.
   Logs are in `data\engine_watchdog.log` and `data\dashboard_watchdog.log`.

4. **Stop Windows from sleeping** while plugged in, or trading pauses whenever
   the PC does:
   ```powershell
   powercfg /change standby-timeout-ac 0
   ```

**To check the tasks are running:** open the Start menu, search "Task
Scheduler", and look for `MemecoinTraderEngine` / `MemecoinTraderDashboard`
in the task list (Status should say "Running").

**To stop everything:**
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\uninstall_scheduled_tasks.ps1
```
This removes the scheduled tasks and stops both processes; `data\trader.db`
(your trade history) is left alone.

**To pick up code updates:** the bot doesn't auto-update — it keeps running
whatever was on disk when it last started. Whenever there's a new change to
grab:
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\update.ps1
```
This pulls the latest code, updates dependencies, and restarts both tasks so
the changes actually take effect.

**Note:** since the tasks trigger "at log on," a PC reboot won't restart the
bot until you log back in. If you want it to survive an unattended reboot
too, the tasks can be changed to trigger "at startup" instead (runs as
SYSTEM rather than your user) — ask if you want that set up.

## Deploying to Fly.io (24/7, requires a card on file)

Fly.io requires a credit/debit card on file for any app — even usage that
stays within its free allowance — so only use this if you're fine with that.
If not, use the Windows PC option above instead; it's genuinely free.

GitHub only holds the code — nothing runs there. To have the bot trade and
be checkable around the clock, it needs to run on a machine that's always
on. This repo is set up to deploy as one small Fly.io app that runs both the
trading loop and the dashboard, backed by a persistent volume so trade
history survives restarts/deploys.

There are two ways to do this. If you connected your Fly.io account to
GitHub through Fly's dashboard ("Launch from GitHub"), **you don't need to
clone this repo at all** — Fly builds and deploys straight from GitHub every
time this branch is pushed. You only need the `fly` CLI for the one-time
setup below (volume + secrets), and those commands work from *any* folder —
you never need `cd` into a local copy of the repo, since they target your
app by name (`--app`), not by local files.

**One-time setup (GitHub-connected app), from any PowerShell window:**

1. **Install the Fly CLI and log in**, if you haven't:
   ```powershell
   pwsh -Command "iwr https://fly.io/install.ps1 -useb | iex"
   fly auth login
   ```
   If `fly` isn't recognized right after installing, close and reopen
   PowerShell (the installer updates PATH, which open windows don't see).

2. **Confirm the app name.** Open the Fly dashboard → your app → the name
   shown at the top must exactly match `app = "..."` in this repo's
   `fly.toml`. It's currently set to `claude-memecoin-trader` — if your
   dashboard shows something different, tell me and I'll push the fix
   (I already have write access to this repo, so you don't need to edit
   `fly.toml` by hand).

3. **Create the persistent volume** for the trade database (needed once;
   the Fly web dashboard doesn't do this, only the CLI does):
   ```powershell
   fly volumes create memecoin_data --app claude-memecoin-trader --region iad --size 1
   ```
   (`--app` must match `fly.toml`'s app name; `--region` must match
   `primary_region` in `fly.toml`, which is `iad`.)

4. **Set secrets.** None are required to run in paper mode, but set
   dashboard credentials since the dashboard will be reachable at a public
   `https://claude-memecoin-trader.fly.dev` URL:
   ```powershell
   fly secrets set DASHBOARD_USERNAME=youruser DASHBOARD_PASSWORD='a-real-password' --app claude-memecoin-trader
   # optional, once you have it:
   fly secrets set TWITTER_BEARER_TOKEN=xxxxx --app claude-memecoin-trader
   ```
   Setting a secret triggers Fly to redeploy the app automatically.

5. **If nothing has deployed yet**, trigger the first deploy from the Fly
   dashboard (an app connected to GitHub usually has a "Deploy" button on
   its overview page) rather than running `fly deploy` locally — there's no
   local checkout for it to deploy from.

**Check on it, from anywhere, forever** (no local clone needed for any of these):
```powershell
fly open --app claude-memecoin-trader
fly logs --app claude-memecoin-trader
fly ssh console --app claude-memecoin-trader -C "python -m memecoin_trader.cli status"
```
Bookmark `https://claude-memecoin-trader.fly.dev` (log in with the
`DASHBOARD_USERNAME`/`DASHBOARD_PASSWORD` you set) to check balance and
trades from your phone any time.

**Updating the deployed bot later:** just push to `claude/cool-tesla-ygjulp`
on GitHub (or have me do it) — Fly redeploys automatically. The volume (and
therefore all trade history) persists across deploys.

<details>
<summary>Alternative: deploying purely from a local clone (no GitHub connection)</summary>

If you'd rather not use Fly's GitHub integration, clone the repo and deploy
directly from your machine instead:
```powershell
git clone https://github.com/thfremont09-oss/memecoin-trader.git
cd memecoin-trader
fly apps create --name claude-memecoin-trader
fly volumes create memecoin_data --app claude-memecoin-trader --region iad --size 1
fly secrets set DASHBOARD_USERNAME=youruser DASHBOARD_PASSWORD='a-real-password' --app claude-memecoin-trader
fly deploy --app claude-memecoin-trader
```
Re-run `fly deploy --app claude-memecoin-trader` from inside that folder
(after `git pull`) any time you want to push a new version.
</details>

**Cost:** a single `shared-cpu-1x`/512MB machine plus a 1GB volume fits
comfortably in Fly's free hobby allowance as of this writing; check Fly's
current pricing page if that matters to you, since it does change.

**Note on `auto_stop_machines = false`** in `fly.toml`: this is required and
intentional. Fly normally scales web apps to zero when idle to save cost,
but this app has a background trading loop that needs to keep running even
when nobody's looking at the dashboard.

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

```powershell
pip install -r requirements-ml.txt   # includes requirements.txt; needed for the ML model tests
pytest
```

Tests cover the ledger's money math, entry/exit strategy rules, the paper
executor's slippage/fee model, technical indicators, the RugCheck client's
parsing, the ML feature extraction and train/predict/save/load roundtrip,
and one full buy-then-exit cycle through the real engine wiring.

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
    rugcheck.py            RugCheck.xyz pre-trade safety client
  analysis/
    indicators.py          SMA, momentum, drawdown, RSI over collected price history
    entry_strategy.py       buy filters (liquidity/volume/age, rug check, ML gate)
    exit_strategy.py        stop-loss / take-profit / trailing-stop / time / rug exits
  ml/
    features.py             entry-time feature vector (signal + market + rug report)
    model.py                 logistic-regression trade-quality model (train/predict/save/load)
  portfolio/
    db.py / models.py / ledger.py   SQLite-backed, Decimal-accurate accounting
  execution/
    base.py               Executor interface, FillResult
    paper_executor.py      simulated fills with realistic slippage
    live_executor.py       real Solana swaps via Jupiter (gated, experimental)
  dashboard/
    server.py + templates/index.html   local FastAPI dashboard
scripts/                  Windows Task Scheduler installer/watchdogs (24/7 without a cloud host)
tests/                    pytest suite
```
