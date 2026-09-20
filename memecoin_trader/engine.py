"""The main loop: watch for hype, screen it against real market data, buy,
then watch open positions and sell when the exit strategy says so."""
from __future__ import annotations

import dataclasses
import logging
import sqlite3
import time
from decimal import Decimal

from memecoin_trader.analysis.entry_strategy import EntryContext, effective_score, evaluate_entry
from memecoin_trader.analysis.exit_strategy import evaluate_exit
from memecoin_trader.config import DB_PATH, MODEL_PATH, TWITTER_SESSION_PATH, EntryConfig, Settings
from memecoin_trader.execution.base import Executor
from memecoin_trader.execution.paper_executor import PaperExecutor
from memecoin_trader.market.dexscreener import DexScreenerClient
from memecoin_trader.market.rugcheck import RugCheckClient
from memecoin_trader.ml.features import extract_features
from memecoin_trader.ml.model import TradeQualityModel
from memecoin_trader.portfolio.db import get_connection, init_db
from memecoin_trader.portfolio.ledger import CAUTION_LEVEL_LABELS, InsufficientCashError, Ledger
from memecoin_trader.signals.base import SignalSource, SocialSignal
from memecoin_trader.signals.mock_source import MockTwitterSource
from memecoin_trader.signals.twitter_source import TwitterAPISource

logger = logging.getLogger(__name__)

DEFAULT_CAUTION_LEVEL = 3

# How much each caution level nudges buy frequency away from config.yaml's
# own entry.mention_score_threshold / timing.token_cooldown_minutes
# (level 3, "Balanced," changes nothing). Deliberately a small, bounded
# range around the config baseline rather than something that could push
# either value to an extreme -- this dial only ever adjusts how often the
# bot buys, never the safety filters (rug checks, ML gate, position sizing)
# that stay fixed regardless of where it's set.
CAUTION_LEVEL_OFFSETS: dict[int, dict[str, float]] = {
    1: {"mention_score_threshold": 10.0, "token_cooldown_minutes": 30.0},  # most cautious: fewer, higher-conviction buys
    2: {"mention_score_threshold": 5.0, "token_cooldown_minutes": 15.0},
    3: {"mention_score_threshold": 0.0, "token_cooldown_minutes": 0.0},  # matches config.yaml as-is
    4: {"mention_score_threshold": -5.0, "token_cooldown_minutes": -15.0},
    5: {"mention_score_threshold": -10.0, "token_cooldown_minutes": -30.0},  # least cautious: more, lower-bar buys
}


def apply_caution_level(entry: EntryConfig, token_cooldown_minutes: float, caution_level: int) -> tuple[EntryConfig, float]:
    """Returns (entry config, token cooldown minutes) adjusted by the given
    caution level, clamped to sane floors/ceilings regardless of the level
    or the config baseline -- belt and suspenders against either landing on
    a pathological value (e.g. a threshold of 0, or a cooldown of 0)."""
    offsets = CAUTION_LEVEL_OFFSETS.get(caution_level, CAUTION_LEVEL_OFFSETS[DEFAULT_CAUTION_LEVEL])
    threshold = entry.mention_score_threshold + offsets["mention_score_threshold"]
    threshold = max(20.0, min(90.0, threshold))
    cooldown = token_cooldown_minutes + offsets["token_cooldown_minutes"]
    cooldown = max(5.0, cooldown)
    return dataclasses.replace(entry, mention_score_threshold=threshold), cooldown


def build_signal_source(settings: Settings, market_client: DexScreenerClient) -> SignalSource:
    if settings.secrets.twitter_bearer_token:
        logger.info("using real Twitter/X API signal source")
        primary: SignalSource = TwitterAPISource(
            config=settings.twitter_signal,
            bearer_token=settings.secrets.twitter_bearer_token,
            chain_id=settings.chain_id,
            market_client=market_client,
        )
    elif settings.scraper_signal.enabled:
        from memecoin_trader.signals.twitter_scraper_source import TwitterScraperSource

        logger.warning(
            "using browser-scraper Twitter signal source — this violates X's Terms of "
            "Service and can get the account whose session is used suspended. Make sure "
            "that's a throwaway account, not your main one."
        )
        primary = TwitterScraperSource(
            config=settings.scraper_signal,
            session_path=TWITTER_SESSION_PATH,
            chain_id=settings.chain_id,
            market_client=market_client,
        )
    else:
        logger.info("no TWITTER_BEARER_TOKEN set — using simulated hype feed over real trending tokens")
        primary = MockTwitterSource(config=settings.mock_signal, chain_id=settings.chain_id, client=market_client)

    sources = [primary]
    if not isinstance(primary, MockTwitterSource) and settings.mock_signal.run_alongside_real:
        logger.info("also running the simulated hype feed alongside %s (mock.run_alongside_real=true)", primary.name)
        sources.append(MockTwitterSource(config=settings.mock_signal, chain_id=settings.chain_id, client=market_client))
    if settings.reddit_signal.enabled:
        if not (settings.secrets.reddit_client_id and settings.secrets.reddit_client_secret):
            logger.warning(
                "signals.reddit.enabled is true but REDDIT_CLIENT_ID/REDDIT_CLIENT_SECRET "
                "are not set — skipping the Reddit signal source"
            )
        else:
            from memecoin_trader.signals.reddit_source import RedditSource

            logger.info("adding Reddit signal source alongside %s", primary.name)
            sources.append(
                RedditSource(
                    config=settings.reddit_signal,
                    secrets=settings.secrets,
                    chain_id=settings.chain_id,
                    market_client=market_client,
                )
            )

    if settings.pumpfun_signal.enabled:
        from memecoin_trader.signals.pumpfun_source import PumpFunLaunchSource

        logger.info("adding pump.fun launch signal source alongside %s", primary.name)
        sources.append(PumpFunLaunchSource(config=settings.pumpfun_signal, chain_id=settings.chain_id))

    if settings.birdeye_signal.enabled:
        if not settings.secrets.birdeye_api_key:
            logger.warning(
                "signals.birdeye.enabled is true but BIRDEYE_API_KEY is not set — "
                "skipping the Birdeye trending signal source"
            )
        else:
            from memecoin_trader.signals.birdeye_source import BirdeyeTrendingSource

            logger.info("adding Birdeye trending signal source alongside %s", primary.name)
            sources.append(
                BirdeyeTrendingSource(
                    config=settings.birdeye_signal,
                    api_key=settings.secrets.birdeye_api_key,
                    chain_id=settings.chain_id,
                )
            )

    if settings.bluesky_signal.enabled:
        from memecoin_trader.signals.bluesky_source import BlueskySource

        logger.info("adding Bluesky signal source alongside %s", primary.name)
        sources.append(
            BlueskySource(config=settings.bluesky_signal, chain_id=settings.chain_id, market_client=market_client)
        )

    if settings.farcaster_signal.enabled:
        if not settings.secrets.neynar_api_key:
            logger.warning(
                "signals.farcaster.enabled is true but NEYNAR_API_KEY is not set — "
                "skipping the Farcaster signal source"
            )
        else:
            from memecoin_trader.signals.farcaster_source import FarcasterSource

            logger.info("adding Farcaster signal source alongside %s", primary.name)
            sources.append(
                FarcasterSource(
                    config=settings.farcaster_signal,
                    api_key=settings.secrets.neynar_api_key,
                    chain_id=settings.chain_id,
                    market_client=market_client,
                )
            )

    if settings.fourchan_signal.enabled:
        from memecoin_trader.signals.fourchan_source import FourChanBizSource

        logger.info(
            "adding 4chan /biz/ signal source alongside %s (corroboration-only, capped low)", primary.name
        )
        sources.append(FourChanBizSource(config=settings.fourchan_signal, chain_id=settings.chain_id))

    if len(sources) == 1:
        return sources[0]
    from memecoin_trader.signals.composite_source import CompositeSignalSource

    return CompositeSignalSource(sources)


def build_executor(settings: Settings) -> Executor:
    if settings.is_live:
        from memecoin_trader.execution.live_executor import LiveExecutor

        return LiveExecutor(config=settings.live_execution, secrets=settings.secrets, mode=settings.mode)
    return PaperExecutor(config=settings.paper_execution)


class TradingEngine:
    def __init__(
        self,
        settings: Settings,
        conn: sqlite3.Connection,
        market_client: DexScreenerClient,
        signal_source: SignalSource,
        executor: Executor,
        rug_client: RugCheckClient | None = None,
        ml_model: TradeQualityModel | None = None,
    ):
        self.settings = settings
        self.conn = conn
        self.ledger = Ledger(conn)
        self.market = market_client
        self.signal_source = signal_source
        self.executor = executor
        self.rug_client = rug_client or RugCheckClient()
        self.ml_model = ml_model
        self._last_prices: dict[str, Decimal] = {}
        self._last_signal_poll = 0.0
        self._last_position_check = 0.0
        self._last_equity_snapshot = 0.0
        self._last_ml_check = 0.0
        self._last_ml_train_dataset_size = 0
        # token_address -> {source_name: last_seen_unix_time}, used to spot
        # a token independently flagged by multiple sources within
        # entry.corroboration_window_minutes -- see effective_score().
        self._recent_signal_sources: dict[str, dict[str, float]] = {}

    def run_forever(self) -> None:
        logger.info(
            "engine starting: mode=%s starting_balance=$%s",
            self.settings.mode,
            self.settings.starting_balance_usd,
        )
        while True:
            try:
                self.tick()
            except Exception:
                logger.exception("unhandled error in engine tick, continuing")
            time.sleep(self.settings.timing.tick_seconds)

    def tick(self) -> None:
        now = time.time()
        # "Offline" only stops new buys — existing positions still get their
        # stop-loss/trailing-stop/rug protection, and the equity chart keeps
        # recording, regardless of this flag.
        if (
            self.ledger.is_trading_enabled()
            and now - self._last_signal_poll >= self.settings.timing.signal_poll_interval_seconds
        ):
            self._poll_signals()
            self._last_signal_poll = now

        if now - self._last_position_check >= self.settings.timing.position_check_interval_seconds:
            self._manage_open_positions()
            self._last_position_check = now

        if now - self._last_equity_snapshot >= self.settings.timing.equity_snapshot_interval_seconds:
            self._record_equity()
            self._last_equity_snapshot = now

        if (
            self.settings.entry.ml.enabled
            and now - self._last_ml_check >= self.settings.entry.ml.retrain_check_interval_minutes * 60
        ):
            self._maybe_retrain_ml_model()
            self._last_ml_check = now

    def _poll_signals(self) -> None:
        try:
            signals = self.signal_source.poll()
        except Exception:
            logger.exception("signal source poll failed")
            return

        # Read fresh every poll (not cached at __init__) so a dashboard
        # slider change takes effect on the very next poll, no restart
        # needed — same immediacy as the online/offline toggle.
        entry_config, token_cooldown_minutes = apply_caution_level(
            self.settings.entry, self.settings.timing.token_cooldown_minutes, self.ledger.get_caution_level()
        )

        for signal in signals:
            market = self.market.get_best_pair_for_token(self.settings.chain_id, signal.token_address)
            if market is not None:
                self._last_prices[signal.token_address] = market.price_usd

            corroborating_sources = self._corroborating_source_count(signal)
            score = effective_score(signal, corroborating_sources, entry_config)

            # Only spend a rug-check call on candidates that already clear the
            # cheap, local hype-score bar — no point querying a third-party
            # API for a signal we'd reject anyway.
            rug_report = None
            if market is not None and score >= entry_config.mention_score_threshold:
                rug_report = self.rug_client.get_risk_report(signal.token_address)
                if rug_report is None and entry_config.rug_check.enabled:
                    logger.warning(
                        "rug check unavailable for %s (%s) — %s",
                        signal.symbol,
                        signal.token_address,
                        "skipping buy (fail_closed)" if entry_config.rug_check.fail_closed
                        else "proceeding without it (fail_closed=false)",
                    )

            features = extract_features(signal, market, rug_report) if market is not None else None

            ml_confidence = None
            if (
                features is not None
                and entry_config.ml.enabled
                and self.ml_model is not None
                and self.ml_model.is_trained
            ):
                ml_confidence = self.ml_model.predict_proba(features)

            ctx = EntryContext(
                cash_usd=self.ledger.get_cash_usd(),
                open_position_count=len(self.ledger.get_open_positions()),
                already_holds_token=self.ledger.get_open_position_for_token(signal.token_address) is not None,
                token_on_cooldown=self.ledger.is_token_on_cooldown(signal.token_address, token_cooldown_minutes),
            )
            decision = evaluate_entry(
                signal,
                market,
                ctx,
                entry_config,
                rug_report=rug_report,
                ml_confidence=ml_confidence,
                corroborating_sources=corroborating_sources,
            )
            self.ledger.record_signal(signal, acted_on=decision is not None)

            if decision is None:
                continue

            logger.info(
                "ENTRY: %s (%s) score=%.1f amount=$%s",
                signal.symbol,
                signal.token_address,
                signal.score,
                decision.amount_usd,
            )
            try:
                fill = self.executor.buy(signal.token_address, decision.amount_usd, market)
                position = self.ledger.open_position(
                    token_address=signal.token_address,
                    symbol=market.symbol or signal.symbol,
                    chain_id=self.settings.chain_id,
                    fill=fill,
                    signal=signal,
                    entry_liquidity_usd=market.liquidity_usd,
                    mode=self.settings.mode,
                )
                if features is not None:
                    self.ledger.save_trade_features(position.id, features)
            except InsufficientCashError as exc:
                logger.warning("skipped buy for %s: %s", signal.symbol, exc)
            except Exception:
                logger.exception("buy execution failed for %s", signal.symbol)

    def _manage_open_positions(self) -> None:
        for position in self.ledger.get_open_positions():
            market = self.market.get_best_pair_for_token(self.settings.chain_id, position.token_address)
            if market is None:
                logger.warning(
                    "no market data for open position %s (%s); skipping this check",
                    position.symbol,
                    position.token_address,
                )
                continue

            previous_liquidity = self.ledger.get_latest_liquidity(position.token_address)
            self._last_prices[position.token_address] = market.price_usd
            self.ledger.record_price_snapshot(
                position.token_address, market.price_usd, market.liquidity_usd, market.volume_24h_usd
            )
            self.ledger.update_peak_price(position, market.price_usd)
            # re-fetch: peak may have just changed
            position = self.ledger.get_open_position_for_token(position.token_address) or position

            decision = evaluate_exit(
                position,
                market.price_usd,
                market.liquidity_usd,
                self.settings.exit,
                previous_liquidity_usd=previous_liquidity,
            )
            if decision is None:
                continue

            sell_quantity = position.quantity * decision.fraction
            logger.info(
                "EXIT (%s): %s (%s) fraction=%s price=$%s",
                decision.reason,
                position.symbol,
                position.token_address,
                decision.fraction,
                market.price_usd,
            )
            try:
                fill = self.executor.sell(position.token_address, sell_quantity, market)
                self.ledger.apply_sell(
                    position=position,
                    fraction=decision.fraction,
                    fill=fill,
                    reason=decision.reason,
                    mark_take_profit_taken=decision.mark_take_profit_taken,
                    mode=self.settings.mode,
                )
            except Exception:
                logger.exception("sell execution failed for %s", position.symbol)

    def liquidate_position(self, token_address: str, reason: str = "manual_sell") -> bool:
        """Sells one open position immediately at the current market price.

        Deliberate, on-demand only (the per-position "Sell" button/CLI
        command) — never called automatically by the engine itself. Returns
        whether the sale actually went through.
        """
        position = self.ledger.get_open_position_for_token(token_address)
        if position is None:
            return False

        market = self.market.get_best_pair_for_token(self.settings.chain_id, token_address)
        if market is None:
            logger.error(
                "cannot sell %s (%s): no market data available right now", position.symbol, token_address
            )
            return False

        try:
            fill = self.executor.sell(position.token_address, position.quantity, market)
            self.ledger.apply_sell(
                position=position,
                fraction=Decimal(1),
                fill=fill,
                reason=reason,
                mark_take_profit_taken=True,
                mode=self.settings.mode,
            )
            logger.info("SOLD (manual): %s (%s) at $%s", position.symbol, token_address, market.price_usd)
            return True
        except Exception:
            logger.exception("manual sell failed for %s", position.symbol)
            return False

    def liquidate_all(self, reason: str = "manual_liquidation") -> int:
        """Sells every open position immediately at the current market price.

        This is a deliberate, on-demand action (the `liquidate` CLI command /
        "Go offline" button) for when you know you're about to be away and
        want to be in cash — never called automatically by the engine
        itself. Returns the number of positions successfully closed.
        """
        closed = 0
        for position in self.ledger.get_open_positions():
            if self.liquidate_position(position.token_address, reason=reason):
                closed += 1
        return closed

    def _record_equity(self) -> None:
        positions_value = Decimal(0)
        for position in self.ledger.get_open_positions():
            price = self._last_prices.get(position.token_address, position.entry_price_usd)
            positions_value += position.quantity * price
        self.ledger.record_equity_snapshot(positions_value)

    def _corroborating_source_count(self, signal: SocialSignal) -> int:
        now = time.time()
        window_seconds = self.settings.entry.corroboration_window_minutes * 60
        per_token = self._recent_signal_sources.setdefault(signal.token_address, {})
        per_token[signal.source] = now
        for source in [s for s, seen_at in per_token.items() if now - seen_at > window_seconds]:
            del per_token[source]
        return len(per_token)

    def _maybe_retrain_ml_model(self) -> None:
        """Automatically (re)trains the ML trade-quality model against the
        bot's own closed-trade history once there's enough of it, so ML
        actually turns on over time instead of requiring someone to
        remember to run `memecoin-trader train` by hand."""
        dataset = self.ledger.get_training_dataset()
        min_required = self.settings.entry.ml.min_training_trades
        if len(dataset) < min_required or len(dataset) == self._last_ml_train_dataset_size:
            return

        features, labels = zip(*dataset)
        if len(set(labels)) < 2:
            return  # need at least one win and one loss to train a classifier

        model = TradeQualityModel()
        try:
            stats = model.train(list(features), list(labels))
        except RuntimeError:
            logger.warning("ML auto-retrain skipped: scikit-learn not installed")
            return
        except ValueError:
            return

        model.save(MODEL_PATH)
        self.ml_model = model
        self._last_ml_train_dataset_size = len(dataset)
        logger.info(
            "auto-trained ML trade-quality model: %d closed trades, %.0f%% holdout accuracy",
            stats["n_samples"],
            stats["accuracy"] * 100,
        )


def create_engine(settings: Settings) -> TradingEngine:
    conn = get_connection(DB_PATH)
    init_db(conn, Decimal(str(settings.starting_balance_usd)))
    market_client = DexScreenerClient()
    signal_source = build_signal_source(settings, market_client)
    executor = build_executor(settings)
    rug_client = RugCheckClient()

    ml_model = TradeQualityModel.load(MODEL_PATH) if settings.entry.ml.enabled else None
    if settings.entry.ml.enabled and ml_model is None:
        logger.info(
            "ML gate is enabled but no trained model found at %s yet — it's a no-op until then; "
            "the engine will auto-train one once %d closed trades exist (or run `memecoin-trader "
            "train` manually now if there's already history from before this was enabled)",
            MODEL_PATH,
            settings.entry.ml.min_training_trades,
        )

    return TradingEngine(settings, conn, market_client, signal_source, executor, rug_client, ml_model)
