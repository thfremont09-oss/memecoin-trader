"""The main loop: watch for hype, screen it against real market data, buy,
then watch open positions and sell when the exit strategy says so."""
from __future__ import annotations

import logging
import sqlite3
import time
from decimal import Decimal

from memecoin_trader.analysis.entry_strategy import EntryContext, evaluate_entry
from memecoin_trader.analysis.exit_strategy import evaluate_exit
from memecoin_trader.config import DB_PATH, MODEL_PATH, TWITTER_SESSION_PATH, Settings
from memecoin_trader.execution.base import Executor
from memecoin_trader.execution.paper_executor import PaperExecutor
from memecoin_trader.market.dexscreener import DexScreenerClient
from memecoin_trader.market.rugcheck import RugCheckClient
from memecoin_trader.ml.features import extract_features
from memecoin_trader.ml.model import TradeQualityModel
from memecoin_trader.portfolio.db import get_connection, init_db
from memecoin_trader.portfolio.ledger import InsufficientCashError, Ledger
from memecoin_trader.signals.base import SignalSource
from memecoin_trader.signals.mock_source import MockTwitterSource
from memecoin_trader.signals.twitter_source import TwitterAPISource

logger = logging.getLogger(__name__)


def build_signal_source(settings: Settings, market_client: DexScreenerClient) -> SignalSource:
    if settings.secrets.twitter_bearer_token:
        logger.info("using real Twitter/X API signal source")
        return TwitterAPISource(
            config=settings.twitter_signal,
            bearer_token=settings.secrets.twitter_bearer_token,
            chain_id=settings.chain_id,
            market_client=market_client,
        )
    if settings.scraper_signal.enabled:
        from memecoin_trader.signals.twitter_scraper_source import TwitterScraperSource

        logger.warning(
            "using browser-scraper Twitter signal source — this violates X's Terms of "
            "Service and can get the account whose session is used suspended. Make sure "
            "that's a throwaway account, not your main one."
        )
        return TwitterScraperSource(
            config=settings.scraper_signal,
            session_path=TWITTER_SESSION_PATH,
            chain_id=settings.chain_id,
            market_client=market_client,
        )
    logger.info("no TWITTER_BEARER_TOKEN set — using simulated hype feed over real trending tokens")
    return MockTwitterSource(config=settings.mock_signal, chain_id=settings.chain_id, client=market_client)


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

    def _poll_signals(self) -> None:
        try:
            signals = self.signal_source.poll()
        except Exception:
            logger.exception("signal source poll failed")
            return

        for signal in signals:
            market = self.market.get_best_pair_for_token(self.settings.chain_id, signal.token_address)
            if market is not None:
                self._last_prices[signal.token_address] = market.price_usd

            # Only spend a rug-check call on candidates that already clear the
            # cheap, local hype-score bar — no point querying a third-party
            # API for a signal we'd reject anyway.
            rug_report = None
            if market is not None and signal.score >= self.settings.entry.mention_score_threshold:
                rug_report = self.rug_client.get_risk_report(signal.token_address)
                if rug_report is None and self.settings.entry.rug_check.enabled:
                    logger.warning(
                        "rug check unavailable for %s (%s) — %s",
                        signal.symbol,
                        signal.token_address,
                        "skipping buy (fail_closed)" if self.settings.entry.rug_check.fail_closed
                        else "proceeding without it (fail_closed=false)",
                    )

            features = extract_features(signal, market, rug_report) if market is not None else None

            ml_confidence = None
            if (
                features is not None
                and self.settings.entry.ml.enabled
                and self.ml_model is not None
                and self.ml_model.is_trained
            ):
                ml_confidence = self.ml_model.predict_proba(features)

            ctx = EntryContext(
                cash_usd=self.ledger.get_cash_usd(),
                open_position_count=len(self.ledger.get_open_positions()),
                already_holds_token=self.ledger.get_open_position_for_token(signal.token_address) is not None,
                token_on_cooldown=self.ledger.is_token_on_cooldown(
                    signal.token_address, self.settings.timing.token_cooldown_minutes
                ),
            )
            decision = evaluate_entry(
                signal, market, ctx, self.settings.entry, rug_report=rug_report, ml_confidence=ml_confidence
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


def create_engine(settings: Settings) -> TradingEngine:
    conn = get_connection(DB_PATH)
    init_db(conn, Decimal(str(settings.starting_balance_usd)))
    market_client = DexScreenerClient()
    signal_source = build_signal_source(settings, market_client)
    executor = build_executor(settings)
    rug_client = RugCheckClient()

    ml_model = TradeQualityModel.load(MODEL_PATH) if settings.entry.ml.enabled else None
    if settings.entry.ml.enabled and ml_model is None:
        logger.info("ML gate is enabled but no trained model found at %s — gate is a no-op until `memecoin-trader train` runs", MODEL_PATH)

    return TradingEngine(settings, conn, market_client, signal_source, executor, rug_client, ml_model)
