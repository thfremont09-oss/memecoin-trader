# Memecoin Trader

A simulation-first bot that watches for social-media FOMO around Solana
memecoins, screens candidates against real market data, and paper-trades
them starting from **$100**. It's built so that going from simulation to
real trading later is a config change, not a rewrite.

## How it works

```
signal source  ──▶  entry strategy  ──▶  paper/live executor  ──▶  ledger (SQLite)
(Twitter/Reddit/mock) (filters:           (fills against real         │
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
| Twitter/X hype signal | **Simulated** by default — see below. A real X API adapter (and a browser-scraper fallback) exists and is a one-line switch away |
| Reddit hype signal | **Enabled but inert until credentialed** — official API, but Reddit now requires a manual, multi-week approval before you can get a key (no more instant self-serve). Runs alongside whichever Twitter/X source is active once set up. See [Reddit](#reddit-official-api-approval-required) |
| Birdeye trending signal | **Enabled but inert until credentialed** — free API key, self-serve, no approval wait. Third-party momentum ranking, runs alongside everything else. See [Birdeye](#birdeye-trending-tokens-free-api-key-no-approval-wait) |
| DexScreener boosted-tokens signal | **On, works immediately** — free, no key, same host as market data; paid-promotion ranking. See [DexScreener boosted tokens](#dexscreener-boosted-tokens-free-no-key--on-by-default) |
| GeckoTerminal trending signal | **On, works immediately** — free, no key, ever. Independent trending ranking, mostly adds corroboration. See [GeckoTerminal](#geckoterminal-trending-pools-free-no-key--on-by-default) |
| Raydium pools-by-volume signal | **On, works immediately** — free, no key, ever. Ranked by 24h volume on one of Solana's biggest AMMs. See [Raydium](#raydium-pools-by-volume-free-no-key--on-by-default) |
| pump.fun launch signal | **Off by default** — free, no-key, real-time on-chain launch feed; highest rug-risk category, so opt-in only. See [pump.fun launch feed](#pumpfun-launch-feed-free-no-key--off-by-default-use-with-caution) |
| Bluesky signal | **On, works immediately** — free, no key, no approval ever (open reads are the protocol's design). See [Bluesky](#bluesky-free-no-key-no-approval--ever) |
| Farcaster signal | **Enabled but inert until credentialed** — free API key via Neynar, self-serve, no approval wait. See [Farcaster](#farcaster-free-api-key-no-approval-wait) |
| 4chan /biz/ signal | **On, works immediately, corroboration-only** — free, no key; capped low so it can never trigger a buy alone. See [4chan /biz/](#4chan-biz-free-no-key--corroboration-only-by-design) |
| Rug-pull screening | **Real** — [RugCheck.xyz](https://rugcheck.xyz) checked before every buy (mint/freeze authority, LP lock, holder risk), plus liquidity/FDV and price-spike heuristics from DexScreener data |
| Trade-quality ML model | **On by default, but a no-op until trained** — auto-trains itself on the bot's own closed-trade history once there's enough of it; see [Machine learning](#machine-learning) |
| Dynamic trailing stop | **Tightens 20% → 15% → 10% → 6%** as a position's peak gain grows, so a big pump gives back less; see [Run away when profits get maximized](#run-away-when-profits-get-maximized-dynamic-trailing-stop) |
| Caution level (buy frequency) | **Live-adjustable, 1-5, default 3 "Balanced"** — dashboard slider or CLI, only affects how often it buys, not safety filters; see [Caution level](#caution-level-buy-frequency-slider) |
| Equity chart zoom | **1MIN / 5M / 1H / 1D / 1W / 1M / YTD / ALL presets** on the dashboard, server-side filtered and downsampled; see [Equity curve zoom](#equity-curve-zoom-1min--5m--1h--1d--1w--1m--ytd--all) |
| Dashboard refresh speed | **Adjustable 1-60s dial**, client-side only; see [Refresh speed dial](#refresh-speed-dial) |
| Confetti | **Fires on any >0.5% equity pop** in a single refresh; see [Confetti on a pop](#confetti-on-a-pop) |
| Dollar-bill shower | **Fires on every profitable sell** — see [Dollar-bill shower on a profitable sell](#dollar-bill-shower-on-a-profitable-sell) |
| Corner mascot crew | **A few dancing friends, each punchable independently** — see [The corner mascot (and his friends)](#the-corner-mascot-and-his-friends) |
| The club | **Cursor-tracked pickup, clubbing a mascot doubles his down-time, 3s-idle auto-return** — see [The club](#the-club) |
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

### Real Twitter/X via browser scraping (no API key, but real risk)

As of February 2026, X has no free API tier at all — reading tweets costs
$0.005/read, pay-per-use, no monthly minimum (see [Going live](#going-live)-style
tradeoffs below). If you'd rather not pay that, there's a third option:
`TwitterScraperSource` (`memecoin_trader/signals/twitter_scraper_source.py`)
logs into a real X account with a real browser (via
[Playwright](https://playwright.dev)) and reads live search results.

**Read this before enabling it.** This is not the paid API — it automates an
X account to read the site, which violates X's Terms of Service regardless
of how gently it's done. Some technical reality behind that:

- The old free scraping tools are dead: `snscrape` has been unmaintained and
  broken since 2023, and public Nitter mirrors are gone (X sent cease-and-desist
  letters). What still works logs into real accounts and replays the app's
  internal API calls.
- **Use a throwaway X account you create just for this — never your main
  one.** The account whose session this uses can be suspended if X's
  automation detection flags it. There's no way to guarantee it won't be. A
  throwaway account means that cost is a nuisance, not a loss of your real
  account, contacts, and history.
- This project deliberately does **not** automate login, CAPTCHA-solving, or
  2FA, and does not run a rotating pool of accounts to dodge bans (a
  technique some scraping guides describe) — that crosses from "personal
  automation" into building infrastructure specifically to evade a
  platform's abuse detection, which isn't something this project does. You
  log into the throwaway account yourself, once, in a real visible browser
  window; the bot only reuses the session that creates.
- Unverified against the live site from the sandbox this was built in (no
  network access there to x.com) — X's page structure may have drifted from
  what's assumed here by the time you run it for real. Watch the logs.
- No engagement counts (likes/retweets) are scraped — that part of X's
  markup is the most brittle and changes often — so signals are scored by
  mention count instead of engagement, unlike the official API source.

**Setup**, once you've decided the throwaway-account risk is acceptable:
```powershell
pip install -r requirements-scraper.txt
playwright install chromium
python scripts/twitter_login_setup.py
```
The last command opens a real browser window to the X login page — log into
your throwaway account there yourself (username/password, CAPTCHA, 2FA,
whatever X asks for), then press Enter in the terminal once you see your
home timeline. It saves the resulting session to `data/twitter_session.json`
(gitignored — never commit it, it's equivalent to a login cookie).

Then set `signals.scraper.enabled: true` in `config.yaml` and restart the
bot. If the session ever expires or the account gets logged out, the logs
will say so clearly (`X session expired or invalid`) — just re-run
`twitter_login_setup.py`.

**Precedence**: a real `TWITTER_BEARER_TOKEN` in `.env` always wins over the
scraper if both are configured; the scraper wins over the simulated feed if
enabled. Only one of these three Twitter/X sources is the "primary" one — but
Reddit (below) runs *alongside* whichever one is active, not instead of it,
and so does the simulated feed if `signals.mock.run_alongside_real: true`
(the default): even once a real source is live, the bot keeps generating
synthetic hype signals on top of real trending Solana tokens too, purely to
keep trade volume up for testing. **Worth knowing:** any trade the mock feed
triggers was acted on because of made-up hype text, not a real signal — it's
clearly logged as `source: twitter_mock` (and the excerpt is prefixed
`[SIMULATED]`) everywhere trades show up, so you can always tell which of
your trades were real-signal-driven vs. synthetic. Set it to `false` if you'd
rather only trade on real signals once you have one configured.

### Reddit (official API, approval required)

Unlike Twitter/X, Reddit's API itself is free and there's no throwaway-account
scraping risk here. `RedditSource` (`memecoin_trader/signals/reddit_source.py`)
scans the subreddits listed in `signals.reddit.subreddits` in `config.yaml`
(defaults: r/CryptoMoonShots, r/solana, r/SatoshiStreetBets, r/CryptoCurrency,
r/pumpfun) for token mentions, scoring by each post's upvotes and comment
count the same way the official Twitter API source scores by likes/retweets.

**Correction from when this section was first written:** Reddit's old
"self-serve, instant client ID/secret" flow is gone. As of its 2026
"Responsible Builder Policy," Reddit requires **explicit pre-approval before
any API access, including personal, non-commercial scripts** — there's no
more just clicking "create app" and getting keys immediately.

**Setup, if you want to pursue it:**
1. Find and submit Reddit's API access request (linked from Reddit's Data
   API Wiki / developer docs — search "Reddit Data API Wiki" if the link
   moves). Describe a personal, non-commercial, **read-only** script that
   polls a handful of subreddits.
2. Wait — reportedly **2-4 weeks** for a manual review, with a real chance
   of rejection or no response at all. This is not a formality.
3. Only once approved: go to https://www.reddit.com/prefs/apps, "create
   app", choose **script**, redirect URI can be `http://localhost:8080`.
4. Copy the client ID (under the app's name) and secret into `.env`:
   ```
   REDDIT_CLIENT_ID=...
   REDDIT_CLIENT_SECRET=...
   ```
   `signals.reddit.enabled` is already `true` in `config.yaml` — with no
   credentials set, the engine just logs a warning and runs on Twitter/mock
   alone, so there's nothing to turn off while you wait.

Both this and the active Twitter/X source feed the same entry strategy, rug
checks, and ML gate — a token still has to clear all of that regardless of
which platform flagged it first.

### Birdeye trending tokens (free API key, no approval wait)

`BirdeyeTrendingSource` (`memecoin_trader/signals/birdeye_source.py`) polls
[Birdeye's](https://docs.birdeye.so) free trending-tokens endpoint —
a third-party momentum ranking independent of DexScreener's own
boosted-tokens list (which the mock feed uses) and of any social-media
signal. It's on by default in `config.yaml` but, like Reddit, does nothing
without a key.

**Setup:**
1. Go to https://birdeye.so/data-api, sign up, and generate a free API key
   — self-serve, no approval wait (unlike Reddit's current process).
2. Add it to `.env`:
   ```
   BIRDEYE_API_KEY=...
   ```
3. Restart the bot. No `config.yaml` change needed — `signals.birdeye.enabled`
   is already `true`.

Tokens are scored by their Birdeye rank (rank 1 = highest score), so the
top ~15-20 trending tokens are the ones actually likely to clear
`entry.mention_score_threshold`. This response shape is taken from
Birdeye's public docs, not verified live from the sandbox this was built
in — if it comes back empty, check the logs for a shape-mismatch warning.

### DexScreener boosted tokens (free, no key — on by default)

`DexScreenerBoostsSource` (`memecoin_trader/signals/dexscreener_boosts_source.py`)
polls DexScreener's own free, public
["token boosts"](https://docs.dexscreener.com/api/reference#token-boosts)
endpoint — token teams pay to get promoted on DexScreener's site, and this
lists whoever's currently spending the most on that. No key, no approval,
uses the exact same host this bot already calls for market data. It's a
paid-promotion signal, not organic momentum, so treat it as one more data
point among many — every other filter (rug check, liquidity, ML gate)
still gates it like any other source. On by default, works immediately.

### GeckoTerminal trending pools (free, no key — on by default)

`GeckoTerminalTrendingSource` (`memecoin_trader/signals/geckoterminal_source.py`)
polls [GeckoTerminal's](https://www.geckoterminal.com/dex-api) free,
public trending-pools endpoint for Solana — a different provider with a
different trending methodology from both Birdeye and DexScreener's
boosts, so it mostly adds independent corroboration weight to tokens the
others already flagged, plus occasionally its own early picks. No key, no
approval, ever. On by default, works immediately.

### Raydium pools by volume (free, no key — on by default)

`RaydiumPoolsSource` (`memecoin_trader/signals/raydium_source.py`) polls
[Raydium's](https://api-v3.raydium.io) own free, public pools API,
ranked by 24h volume. Raydium is one of the two or three biggest Solana
AMMs and where a lot of pump.fun graduates and other memecoins end up
listed, so this is "where the real trading is happening right now"
rather than a third-party trending guess. No key, no approval, ever. On
by default, works immediately.

All three of the above are scored the same way — rank 1 (top of that
provider's list) scores highest, tapering off after roughly the top
15-20. Their response shapes are taken from each provider's public docs,
not verified live from the sandbox this was built in — if any comes back
empty, check the logs for a shape-mismatch warning.

### pump.fun launch feed (free, no key — off by default, use with caution)

`PumpFunLaunchSource` (`memecoin_trader/signals/pumpfun_source.py`) connects
to [PumpPortal's](https://pumpportal.fun) free public WebSocket
(`wss://pumpportal.fun/api/data`, no key, no cost) and watches pump.fun
token-creation events in real time — literally "a token was just launched,"
not social hype about an existing one. It's the earliest possible discovery
signal, and also the **highest rug/scam-risk category on Solana** — most
pump.fun launches are abandoned or drained within minutes. Because of that
it's **off by default**.

It doesn't treat a launch as tradeable the moment it sees it: each new mint
sits in an in-memory buffer for `signals.pumpfun.min_age_minutes` (default
8, kept above `entry.min_pair_age_minutes`) before it's even emitted as a
signal, and from there it still has to clear every other filter — liquidity,
RugCheck, the ML gate — exactly like a Twitter- or Reddit-sourced signal
does. A launch that never gets a real DexScreener listing within
`signals.pumpfun.max_buffer_minutes` (default 120) is quietly dropped, not
bought.

**To enable:** set `signals.pumpfun.enabled: true` in `config.yaml` and
restart — no API key needed. PumpPortal's exact message schema (`mint`,
`symbol`, `name` fields) is taken from its public docs/community examples,
not verified live from this sandbox; watch the logs the first time it runs
for real.

### Bluesky (free, no key, no approval — ever)

`BlueskySource` (`memecoin_trader/signals/bluesky_source.py`) polls
Bluesky's fully public `searchPosts` endpoint. Unlike X/Twitter, open
unauthenticated reads are the AT Protocol's actual design intent — there's
no approval gate, no key, and no ToS-violation risk comparable to the
Twitter scraper, because permissionless public reads are the point of the
protocol. On by default, works immediately, no setup.

### Farcaster (free API key, no approval wait)

`FarcasterSource` (`memecoin_trader/signals/farcaster_source.py`) searches
casts via [Neynar](https://neynar.com), the standard developer platform
built on top of Farcaster's protocol (which doesn't support full-text
search on its own). Needs a free, self-serve `NEYNAR_API_KEY` — sign up at
neynar.com, no approval wait like Reddit. On by default in `config.yaml`
but inert without the key. Worth knowing: Farcaster's community skews
Ethereum/Base rather than Solana-specific, so expect fewer hits here than
the other sources for this bot's Solana-only universe — it's still a real,
independent signal for whatever crossover chatter exists.

### 4chan /biz/ (free, no key — corroboration-only by design)

`FourChanBizSource` (`memecoin_trader/signals/fourchan_source.py`) reads
4chan's free, public, unauthenticated `/biz/` catalog — a long-standing
origin point for early shitcoin/memecoin chatter, arguably predating
crypto Twitter for this exact purpose. No key, no login, no approval.

**This one is deliberately wired to never trigger a buy on its own.**
`/biz/` is anonymous and often adversarial — posting a contract address as
a joke, or specifically to bait newcomers into buying something about to
be dumped on, is common there. Its signal score is capped low enough
(`MAX_SIGNAL_SCORE = 25.0` in the source file) that even after
`entry.corroboration_bonus_score` (+15 by default) is added, it still
can't clear a sane `entry.mention_score_threshold` (55 by default) by
itself. What it *can* do is add to the "how many distinct sources have
flagged this token recently" count — so if /biz/ and a real source (say,
Bluesky) both flag the same token, /biz/'s mention helps push the *real*
source's signal over the threshold via the corroboration bonus, without
ever being trusted as a signal on its own. If you ever tune
`mention_score_threshold` dramatically lower than the default, revisit
this cap — the safety property only holds at realistic threshold values.

### Telegram (free API credentials, one-time login — off by default)

`TelegramSource` (`memecoin_trader/signals/telegram_source.py`) reads public
Telegram channel messages via [Telethon](https://docs.telethon.dev), the
same official MTProto client library the real Telegram app itself uses —
not scraping, and not the bot-in-a-server route (most worthwhile
alpha/gem-call channels ban bots specifically to prevent that; this reads
as a real logged-in user account instead, the same way you'd read the
channel in the app). Solana memecoin "gem call" channels are one of the
most active sources of this kind of hype, often earlier than Twitter or
Reddit.

Setup, two steps:
1. Get a free `api_id`/`api_hash` at [my.telegram.org](https://my.telegram.org)
   (log in with your phone number → API development tools → create an app)
   and set `TELEGRAM_API_ID`/`TELEGRAM_API_HASH` in `.env`.
2. Run `python scripts/telegram_login_setup.py` once — it prompts for your
   phone number, the login code Telegram texts you, and your 2FA password if
   you have one set, then saves a reusable session to `data/`.

Then set `signals.telegram.enabled: true` in `config.yaml` and list the
channel usernames you want to watch under `signals.telegram.channels`
(without the `@`). Off by default (unlike Reddit/Birdeye/Farcaster, which
are on-by-default-but-inert without credentials) because it needs that
one-time interactive login before it can do anything at all.

**Not integrated, and why:** Truth Social has no public API either, so
scanning it would mean the same throwaway-account browser scraping as
Twitter/X, for a platform with much less crypto-trading chatter — not worth
repeating that risk for. Token Sniffer only scans EVM chains (Ethereum,
BSC, etc.) and can't check Solana tokens, which is what this bot trades —
RugCheck.xyz (already integrated) covers the Solana-specific equivalent
(mint/freeze authority, LP locks). GMGN.AI has no public API either, only
private endpoints that would need reverse-engineering. Photon and BullX are
trading terminals/UI, not data providers — they don't expose anything this
bot doesn't already get from DexScreener. Reddit's classic Data API is
being wound down for new external-data use cases in favor of Devvit
(Reddit's in-platform app framework, which can't serve data out to an
external bot like this one) — not worth pursuing further; see the
[Reddit](#reddit-official-api-approval-required) section above for the
full story. Discord alpha-calling servers are a real source of hype but
need admin access to add a bot (most worthwhile servers ban bots
specifically to prevent this) — ask if you have a specific server you can
actually get a bot into.

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
  finding blocks the trade by default (`entry.rug_check.max_danger_flags`),
  and so does piling up too many smaller "warning"-level findings even with
  zero danger flags (`entry.rug_check.max_warning_flags`, default 2). The
  mint-authority and freeze-authority checks are also applied directly as
  their own hard block (`mint_authority_renounced`/`freeze_authority_renounced`
  is explicitly `False`) rather than relying only on RugCheck's own
  danger/warning labeling of them — belt and suspenders on the two most
  unambiguous rug vectors.
- **Fails closed by default** (`entry.rug_check.fail_closed: true`): if the
  check errors out or the token isn't indexed yet, the trade is skipped
  rather than assumed safe. This means if RugCheck.xyz is ever unreachable,
  the bot will simply stop buying anything until it's reachable again —
  check the logs for `rug check unavailable` if entries seem to have
  stopped. Set `fail_closed: false` if you'd rather it trade through that.
- **LP-locked floor** (`entry.rug_check.min_lp_locked_pct`, default 75%):
  requires at least this much LP actually locked/burned, when RugCheck
  reports it — an unlocked pool means the deployer can pull all liquidity
  whenever they want.
- **Liquidity-to-FDV ratio** (`entry.min_liquidity_to_fdv_pct`, default 3%):
  skips tokens whose liquidity is a razor-thin sliver of their reported
  valuation — an easy setup to manipulate or rug.
- **Price-spike cap** (`entry.max_price_change_5m_pct`): skips tokens that
  already spiked hard in the last 5 minutes, so the bot isn't chasing the
  top of a pump.
- **Buy/sell pressure** (`entry.min_buy_sell_ratio`, default 1.0): skips
  tokens where DexScreener's 24h sell count already exceeds the buy count by
  more than this ratio allows — more selling than buying is a distribution/
  dumping pattern, not accumulation. This data was already being fetched and
  fed to the ML model as a feature; it just wasn't a rule-based filter until
  now.
- **Catastrophic liquidity-drop exit** (`exit.catastrophic_liquidity_drop_pct`,
  default 70%): for positions already held, an instant exit fires if
  liquidity craters compared to the literal immediately-previous poll,
  however recent that was — this is the fast path for a real LP pull, which
  can drain a pool within a single tick. The bar is set high (70% in one
  tick) so ordinary trade-impact noise essentially never trips it.
- **Sudden liquidity-drop exit** (`exit.sudden_liquidity_drop_pct`, default
  45%): a second, wider-window emergency exit fires if liquidity drops
  sharply compared to a reading from `timing.liquidity_rug_check_interval_seconds`
  ago (default 30s) — catching a rug that drains more gradually than the
  catastrophic check's single-tick bar, still faster than waiting for the
  cumulative decline-from-entry check (`exit.liquidity_rug_fraction`, default
  50%) to cross its floor. This comparison window is deliberately decoupled
  from `timing.position_check_interval_seconds` (default 5s, tuned purely for
  how often the dashboard refreshes) — comparing two readings only 5s apart
  was firing on ordinary thin-pool price-impact noise, not just real rugs.
  Between the two checks: a genuinely fast, near-total drain is caught
  instantly by the catastrophic check regardless of how young the position
  is; a rug that plays out a bit slower, or partially, is still caught by
  the windowed check without either one flagging normal volatility.

None of the above trades safety for trade volume — every extra signal
source added still has to clear every one of these filters. The knobs that
actually increase how often the bot trades are elsewhere: it polls signal
sources more often (`timing.signal_poll_interval_seconds`, 30s), can hold
more positions at once (`entry.max_open_positions`, 8), re-enters a
previously-sold token sooner (`timing.token_cooldown_minutes`, 30min), and
pulls a larger candidate list per poll from Reddit/Birdeye/the scraper. More
candidates funneled through the same, now-tighter safety gate is the actual
goal — not a looser gate.

This integration is unverified against RugCheck's live API from the sandbox
this was built in (its outbound network is restricted) — the parsing is
deliberately defensive, but watch the logs the first few times it runs for
real and tell me if anything about the response shape looks off.

## Multi-source corroboration

With multiple independent hype/discovery sources now running at once
(Twitter/X, Reddit, Birdeye, DexScreener boosts, GeckoTerminal, Raydium,
Bluesky, Farcaster, 4chan /biz/, the mock feed, optionally pump.fun and
Telegram), the bot
tracks which sources have flagged each token recently
(`entry.corroboration_window_minutes`, default 30). If 2 or more distinct
sources independently flag the same token within that window, its score
gets a bonus (`entry.corroboration_bonus_score`, default +15) before it's
checked against `entry.mention_score_threshold` — a token two unrelated
research angles agree on is stronger evidence than either alone, so a
signal too weak by itself can still clear the bar once corroborated. A
single strong signal from one source still gets through on its own merits;
this only ever helps a borderline case, never blocks anything.

## Per-position detail view

Clicking a coin's symbol — in either the open-positions table or the recent
trades table — opens a detail view for that specific position: not general
information about the token, but its own journey since you bought it. It
shows entry vs. current (or exit) price, quantity, cost basis, fees paid,
realized/unrealized P&L, every fill against that position (the entry buy,
any partial take-profit sells, the final exit), and a price chart spanning
just that position's own lifetime — from the moment it was opened to now
(or to when it closed). A trade row in the recent-trades table links back
to the same position, so a closed position's story is just as easy to pull
up as an open one's. Backed by `GET /api/position/{id}`
(`memecoin_trader/reporting.py`'s `build_position_detail`).

## Equity curve zoom (1MIN / 5M / 1H / 1D / 1W / 1M / YTD / ALL)

The dashboard's equity chart has a row of zoom presets above it, the same
idea as a stock app's chart range buttons. Clicking one re-fetches just
that window and redraws the chart immediately (not waiting for the next
refresh tick); the auto-refresh then keeps redrawing at whatever range
is currently selected as new snapshots come in.

Every preset is served by the same `/api/summary?range=<key>` endpoint
(`1min`, `5m`, `1h`, `1d`, `1w`, `1m`, `ytd`, `all`), which filters
`equity_history` by `recorded_at` server-side rather than shipping the
whole table to the browser and filtering there. A window with more than
500 snapshots in it (e.g. a month at the default 5-second snapshot
interval) is evenly downsampled to 500 points — always keeping the most
recent point exact — so "1M" or "ALL" stays fast and the chart doesn't
try to render hundreds of thousands of points. Defaults to `all` (today's
full history) if you load the page or hit the API with no `range` at
all, or with one it doesn't recognize.

## Refresh speed dial

Next to the subtitle at the top of the dashboard is a small slider (1-60s)
that controls how often the page re-polls `/api/summary` — was a fixed 5s
for everyone, now yours to set. It's a client-side-only preference (saved
in the browser's `localStorage`, not the database), so it's per-device and
takes effect on the very next tick with no page reload. If browser storage
is unavailable (private browsing, blocked site data), the slider still
works for that session, it just won't be remembered next time.

The equity chart tracks this same rate, not just the number cards. The
engine only writes a new `equity_history` row every
`timing.equity_snapshot_interval_seconds` (5s by default) regardless of
what the dial is set to, so on every poll the chart appends (or refreshes)
one extra point for "right now" using the live equity value the same
response already carries — the line keeps advancing at the dial's pace
even between the engine's own periodic snapshots, and true persisted
history replaces it as soon as the next real snapshot lands.

## Confetti on a pop

Any single refresh where total equity jumps by more than 0.5% since the
previous one — a token you're holding just spiked — fires a two-second
confetti burst over the page. It's a plain `<canvas>` overlay written by
hand (no external library/CDN), so it works offline and needs nothing
installed. A 15-second cooldown keeps a sustained rally from firing a new
burst on every single tick; it can still fire again and again across a
longer rally, just not back-to-back.

## Dollar-bill shower on a profitable sell

Whenever the bot actually closes out a sell trade at a profit (positive
`realized_pnl_usd`, not just equity ticking up from a mark-to-market
price move like the confetti above), a shower of 💵 bills rains down the
screen for a few seconds — same hand-rolled canvas-overlay technique as
the confetti, so no extra library. The dashboard tracks the highest
trade id it's already reacted to and only fires on new profitable sells
it hasn't seen yet, so a trade never re-triggers the shower on a later
refresh, and several profitable sells landing in the same 5-second
window still just fire once.

## The corner mascot (and his friends)

Bottom-right corner of the dashboard: a little crew of hand-drawn
pixel-art dudes, each on his own 32x40 `<canvas>` scaled up 3x with
`image-rendering: pixelated` for a chunky, retro sprite look (a face with
eyes and a mouth, a hairstyle, distinct hands and shoes — think an old
NES victory-dance animation, not a static emoji). Each friend has his own
color palette (different hair/shirt/pants) so they read as distinct
people, not clones, and their dance poses are slightly out of phase with
each other so the group doesn't move in unison like a chorus line.

While total return is flat or positive they all cycle through a 4-pose
dance loop — arms and legs swap sides and the torso leans the other way
each beat, ~6 poses/sec — and the moment it dips into the red they switch
to a slow, desaturated 2-frame droop instead: head and shoulders sink and
settle, arms hanging at their sides. No images, sprite sheets, or
external libraries — every pose is just a handful of `fillRect()` calls
per frame — and it updates on the same `total_return_usd >= 0` rule the
rest of the page already uses for its green/red split. Which pose set is
playing is also reflected in `#mascotGroup`'s `data-mode` attribute
(`dance` or `sad`) if you want to hook into it yourself.

**Click any one of them.** That friend tumbles over, lands dazed (little
stars circling his head, still in his own colors), then sits and cries
actual tears for 5 seconds — regardless of whether the group is actually
up or down at the moment — before picking himself back up into whatever
the group's normal state (dancing or sad) currently is. Each friend
tracks his own `fallenAt` timestamp independently, so punching one
doesn't interrupt the others, and clicking him again mid-cry just resets
his own down-time clock rather than needing any special-case handling.
Every hit — bare-handed punch or [club](#the-club) — also leaves a
little cartoon bump (a bruise with a couple of impact sparks) on the
victim's head for as long as he's down, drawn as part of the same
tumble/dazed/cry poses. A club hit keeps him down twice as long as a
punch — see below.

## The club

Bottom-left corner (opposite the mascots): a small pixel-art club, idle
and waiting. Click it to actually pick it up — it then follows your
mouse cursor around the whole page (a second, bigger canvas tracks
`mousemove`) instead of sitting still, so you aim it yourself. Move it
over a mascot and click him to actually club him — the club visibly
swings, sweeping through a rotating arc (wound back to full
follow-through, ~220ms) rather than just silently registering the hit.
The club itself ignores clicks while held (`pointer-events: none`), so
your click passes straight through it to whichever mascot is underneath,
rather than the club intercepting its own click. The held/swinging
canvas is drawn oversized with the club's grip pinned to a fixed pivot
point so the rotation has room to sweep without clipping outside it —
the plain idle canvas back in the corner never rotates and stays small.

A club hit is a much harder knockdown than a bare-handed punch: a direct
click on a mascot with no club in hand keeps him down for 5 seconds
(`PUNCH_DOWN_MS`); clubbing him keeps him down for 10 seconds instead
(`CLUB_DOWN_MS`) — that's the "cooldown" in the down-time sense, not a
lockout on the club itself. Each hit resets a 3-second "still in hand"
timer; let 3 seconds pass with no further hit and the club teleports
back to its idle spot in the corner, ready to be picked up again
immediately — no waiting period on the club, ever. State machine:
`idle` → (click the club) → `held`, tracking the cursor → (click a
mascot) → that mascot goes down for 10s, reset the 3s timer, stay
`held` → (3s pass with no hit) → back to `idle` in the corner.

## Caution level (buy-frequency slider)

A 5-position slider at the top of the dashboard (and a `caution` CLI
command) that controls how often the bot opens new positions, without
touching anything safety-related:

| Level | Label | vs. config.yaml baseline |
|---|---|---|
| 1 | Very cautious | score threshold +10, re-buy cooldown +30 min |
| 2 | Cautious | score threshold +5, re-buy cooldown +15 min |
| 3 | Balanced (default) | unchanged — exactly what `config.yaml` says |
| 4 | Active | score threshold -5, re-buy cooldown -15 min |
| 5 | Aggressive | score threshold -10, re-buy cooldown -30 min |

It only ever nudges `entry.mention_score_threshold` (how strong a hype
signal has to be before it's considered) and `timing.token_cooldown_minutes`
(how soon it'll re-buy a token it just sold) — both bounded so they can
never land on an extreme regardless of level or config. Everything else
— rug checks, liquidity/FDV filters, LP-lock and mint/freeze-authority
checks, position sizing, stop-loss/take-profit, the ML confidence gate —
stays exactly as configured no matter where the slider sits.

Changes take effect on the very next signal poll, no restart needed,
because the engine re-reads the stored level from the database on every
poll. Set it from the dashboard slider, or from the command line:

```powershell
# Show the current level and all options
python -m memecoin_trader.cli caution

# Set it (1-5; out-of-range values are clamped)
python -m memecoin_trader.cli caution 2
```

## BIG RISK

A red hazard-striped button next to the caution slider. Clicking it:

1. **Sells every open position immediately.**
2. **Arms a search window** (`big_risk.search_window_seconds`, default 5
   minutes) during which the engine polls every signal source exactly like
   normal, but skips `entry.mention_score_threshold` and the usual position
   sizing entirely — the first signal that clears every *other* safety
   filter (RugCheck, liquidity/volume/age, buy/sell pressure, the ML gate
   if enabled) gets the entire cash balance. Red siren lights flash across
   the page while this is happening (with a synthesized wailing siren
   sound — a tone oscillator swept by an LFO via the Web Audio API, no
   audio file involved — mutable with the 🔊 button on the status banner,
   which remembers your preference), and a status banner shows a live
   countdown.
3. **If nothing clears the filters before the window runs out, it gives up
   automatically** and the bot resumes its normal multi-source strategy —
   nothing gets bought.
4. **Once a target is found and bought, the sirens keep going** and the
   status banner tracks the position's live P&L. It's still protected: the
   normal rug-detection and trailing-stop/take-profit exits all still
   apply, plus a dedicated, tighter stop-loss just for this mode
   (`big_risk.stop_loss_pct`, default 15% vs. the normal 25%) — since
   you're all-in on one coin, cutting losses faster matters more here.
5. **The instant that position fully closes** — this stop-loss, a normal
   exit, or a manual sell — **the bot resumes normal trading automatically.**

The button doubles as a cancel/sell control depending on where things
stand: **ABORT** while searching (calls off the hunt, nothing bought yet)
or **SELL** while all-in (sells the position immediately, same as the
per-position Sell button). Backed by `POST /api/big-risk/start` /
`/cancel` / `/stop` and `TradingEngine._big_risk_search` /
`_big_risk_manage_position` in `memecoin_trader/engine.py`.

## Machine learning

`memecoin_trader/ml/` adds a small logistic-regression model that predicts,
from a token's entry-time features (hype score, liquidity, volume, price
momentum, buy/sell ratio, RugCheck score, how many independent sources
corroborated the signal, etc.), the probability a trade will end up
profitable. Since a rug pull always shows up as a large loss, a
model that predicts plain profitability is implicitly learning to avoid
rug-like patterns too — there's no separate "is this a scam" label needed.
scikit-learn/joblib are regular dependencies now (in `requirements.txt`),
so nothing extra needs installing for this.

**It trains itself automatically.** `entry.ml.enabled: true` by default, but
it's a no-op until there's a model — every buy the bot makes automatically
saves its entry-time feature vector, and once
`entry.ml.min_training_trades` (default 30) closed, feature-tagged trades
exist, the engine trains a logistic regression model on its own, saves it
to `data/model.joblib`, and starts using it immediately — no CLI command,
no restart needed. It keeps re-checking every
`entry.ml.retrain_check_interval_minutes` (default 60) and only actually
retrains when there's new closed-trade history since the last run, so it
stays current as the bot accumulates more experience. Once live, the
model's predicted confidence (`entry.ml.min_confidence`, default 0.60) is
an additional gate on top of every other filter above, not a replacement
for any of them.

You can still train on demand instead of waiting for the next automatic
check (e.g. right after crossing the minimum trade count):
```powershell
python -m memecoin_trader.cli train
```

## Run away when profits get maximized (dynamic trailing stop)

The flat `exit.trailing_stop_pct` (20% by default) always gave back the
same fraction of a position's peak gain before exiting, whether that peak
was +10% or +300% — a real moonshot could roll over and hand back a huge
chunk of the win before the flat trailing stop finally triggered.
`exit.trailing_stop_tiers` in `config.yaml` tightens the trailing stop as
a position's best-ever gain grows, so the bigger the pump, the less of it
gets given back before the bot locks in profit and gets out:

| Position's peak gain reached... | Trailing stop tightens to |
|---|---|
| below 50% | 20% (the flat baseline) |
| 50%+ | 15% |
| 100%+ (doubled) | 10% |
| 200%+ (tripled) | 6% |

Whichever tier's threshold the position's peak has reached *and* is the
tightest applies — a token that peaked at +250% uses the 6% tier, not the
looser 15%/10% ones it also technically qualifies for. This only ever
affects how a profitable position is protected on the way out; it never
loosens the stop-loss, the liquidity-rug exits, or anything else that
already runs ahead of it in `evaluate_exit()`'s priority order. Tune or
add tiers in `config.yaml`'s `exit.trailing_stop_tiers` list.

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

# In another terminal: check balance / positions / P&L, plus a
# performance-by-signal-source breakdown (win rate, total/avg P&L per
# source, once you have closed trades) -- the actual answer to "which
# of Twitter/Reddit/Birdeye/etc. is worth keeping" instead of a guess
python -m memecoin_trader.cli status

# Or launch the web dashboard (auto-refreshing balance, equity chart,
# open positions, trade history, same per-source breakdown) at
# http://127.0.0.1:8787
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

# Add cash to the simulated portfolio without touching trade history
# (also raises the "starting balance" baseline by the same amount, so
# Total return keeps measuring actual trading performance, not the deposit)
python -m memecoin_trader.cli deposit 20
```

(If you installed the package with `pip install -e .`, you can also just
run `memecoin-trader run` / `status` / `dashboard` / `sell` / `offline` /
`online` / `liquidate` / `reset` / `deposit`.)

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
   The most reliable log is `data\trader.log` — it's written directly by
   Python, so it always shows what the engine and dashboard are actually
   doing (e.g. `Get-Content data\trader.log -Tail 20 -Wait`). There are also
   `data\engine_watchdog.log` / `data\dashboard_watchdog.log` (just
   start/restart lifecycle messages from the wrapper script) and
   `data\engine_stdout.log` / `data\engine_stderr.log` /
   `data\dashboard_stdout.log` / `data\dashboard_stderr.log` (raw
   console output from each process, useful if one won't start at all).

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

After changing `portfolio.starting_balance_usd` or anything else you want
the simulation to reflect from a clean slate, wipe the old trade history and
restart from the new numbers with:
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\update.ps1 -Reset
```
(or `python -m memecoin_trader.cli reset --yes` directly if you're not using
the 24/7 Windows setup). This permanently deletes all simulated trades,
positions, and equity history — the starting cash is the only thing carried
forward, from `config.yaml`.

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
pip install -r requirements.txt
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
    text_extraction.py    shared token-address/cashtag extraction (API + scraper sources)
    mock_source.py        simulated hype feed over real trending tokens
    twitter_source.py     real X API v2 adapter (needs TWITTER_BEARER_TOKEN)
    twitter_scraper_source.py  real X via browser automation (needs a throwaway account + session)
    reddit_source.py       real Reddit via its free official API (needs approval + REDDIT_CLIENT_ID/SECRET)
    birdeye_source.py      Birdeye's free trending-tokens API (needs BIRDEYE_API_KEY)
    pumpfun_source.py      pump.fun launches via PumpPortal's free WebSocket (off by default, no key needed)
    bluesky_source.py      Bluesky's free public searchPosts API (no key, ever)
    farcaster_source.py    Farcaster casts via Neynar's free-tier API (needs NEYNAR_API_KEY)
    fourchan_source.py     4chan /biz/ catalog (no key; corroboration-only, capped low)
    telegram_source.py     Telegram channels via Telethon (needs TELEGRAM_API_ID/HASH + one-time login, off by default)
    composite_source.py    merges multiple signal sources (e.g. Twitter + Reddit + Birdeye) into one
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
scripts/                  Windows Task Scheduler installer/watchdogs (24/7 without a cloud host),
                          twitter_login_setup.py (one-time manual X login for the browser scraper),
                          telegram_login_setup.py (one-time manual Telegram login for the Telegram source)
tests/                    pytest suite
```
