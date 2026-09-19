"""Loads config.yaml + .env into typed, immutable settings objects."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"
# Overridable so a deployment can point this at a mounted persistent volume
# (e.g. Fly.io's /data) instead of the repo checkout.
DATA_DIR = Path(os.environ.get("MEMECOIN_DATA_DIR", str(PROJECT_ROOT / "data")))
DB_PATH = DATA_DIR / "trader.db"
LOG_PATH = DATA_DIR / "trader.log"
MODEL_PATH = DATA_DIR / "model.joblib"
TWITTER_SESSION_PATH = DATA_DIR / "twitter_session.json"


@dataclass(frozen=True)
class TimingConfig:
    tick_seconds: int
    signal_poll_interval_seconds: int
    position_check_interval_seconds: int
    equity_snapshot_interval_seconds: int
    token_cooldown_minutes: int


@dataclass(frozen=True)
class RugCheckConfig:
    enabled: bool
    fail_closed: bool
    max_danger_flags: int
    max_warning_flags: int
    min_lp_locked_pct: float


@dataclass(frozen=True)
class MlConfig:
    enabled: bool
    min_confidence: float
    min_training_trades: int


@dataclass(frozen=True)
class EntryConfig:
    mention_score_threshold: float
    min_liquidity_usd: float
    min_volume_24h_usd: float
    min_pair_age_minutes: float
    max_pair_age_hours: float
    max_open_positions: int
    position_size_pct_of_cash: float
    min_trade_usd: float
    max_trade_usd: float
    min_liquidity_to_fdv_pct: float
    max_price_change_5m_pct: float
    rug_check: RugCheckConfig
    ml: MlConfig


@dataclass(frozen=True)
class ExitConfig:
    stop_loss_pct: float
    take_profit_pct: float
    take_profit_sell_fraction: float
    trailing_stop_pct: float
    max_hold_minutes: float
    liquidity_rug_fraction: float
    sudden_liquidity_drop_pct: float


@dataclass(frozen=True)
class PaperExecutionConfig:
    base_slippage_bps: float
    slippage_impact_factor: float
    fee_bps: float


@dataclass(frozen=True)
class LiveExecutionConfig:
    max_trade_usd: float
    slippage_bps: float
    priority_fee_lamports: int


@dataclass(frozen=True)
class MockSignalConfig:
    trending_refresh_minutes: float
    max_signals_per_poll: int
    run_alongside_real: bool


@dataclass(frozen=True)
class TwitterSignalConfig:
    query: str
    max_results_per_poll: int


@dataclass(frozen=True)
class ScraperSignalConfig:
    enabled: bool
    search_query: str
    headless: bool
    max_tweets_per_poll: int
    mention_cooldown_minutes: float


@dataclass(frozen=True)
class RedditSignalConfig:
    enabled: bool
    subreddits: list[str]
    max_posts_per_poll: int
    mention_cooldown_minutes: float


@dataclass(frozen=True)
class PumpFunSignalConfig:
    enabled: bool
    min_age_minutes: float
    max_buffer_minutes: float


@dataclass(frozen=True)
class BirdeyeSignalConfig:
    enabled: bool
    limit: int
    mention_cooldown_minutes: float


@dataclass(frozen=True)
class Secrets:
    twitter_bearer_token: str | None
    reddit_client_id: str | None
    reddit_client_secret: str | None
    reddit_user_agent: str
    birdeye_api_key: str | None
    solana_private_key: str | None
    solana_rpc_url: str
    live_trading_confirmed: bool


@dataclass(frozen=True)
class Settings:
    mode: str
    starting_balance_usd: float
    chain_id: str
    timing: TimingConfig
    entry: EntryConfig
    exit: ExitConfig
    paper_execution: PaperExecutionConfig
    live_execution: LiveExecutionConfig
    mock_signal: MockSignalConfig
    twitter_signal: TwitterSignalConfig
    scraper_signal: ScraperSignalConfig
    reddit_signal: RedditSignalConfig
    pumpfun_signal: PumpFunSignalConfig
    birdeye_signal: BirdeyeSignalConfig
    secrets: Secrets
    raw: dict[str, Any] = field(repr=False)

    @property
    def is_live(self) -> bool:
        return self.mode == "live"


def _load_yaml(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_settings(config_path: Path | None = None, env_path: Path | None = None) -> Settings:
    load_dotenv(dotenv_path=env_path or (PROJECT_ROOT / ".env"))
    raw = _load_yaml(config_path or DEFAULT_CONFIG_PATH)

    mode_from_env = os.environ.get("MODE")
    mode = (mode_from_env or raw.get("mode") or "paper").strip().lower()

    timing = TimingConfig(**raw["timing"])
    entry_raw = dict(raw["entry"])
    rug_check = RugCheckConfig(**entry_raw.pop("rug_check"))
    ml = MlConfig(**entry_raw.pop("ml"))
    entry = EntryConfig(rug_check=rug_check, ml=ml, **entry_raw)
    exit_cfg = ExitConfig(**raw["exit"])
    paper_exec = PaperExecutionConfig(**raw["execution"]["paper"])
    live_exec = LiveExecutionConfig(**raw["execution"]["live"])
    mock_signal = MockSignalConfig(**raw["signals"]["mock"])
    twitter_signal = TwitterSignalConfig(**raw["signals"]["twitter"])
    scraper_signal = ScraperSignalConfig(**raw["signals"]["scraper"])
    reddit_signal = RedditSignalConfig(**raw["signals"]["reddit"])
    pumpfun_signal = PumpFunSignalConfig(**raw["signals"]["pumpfun"])
    birdeye_signal = BirdeyeSignalConfig(**raw["signals"]["birdeye"])

    secrets = Secrets(
        twitter_bearer_token=os.environ.get("TWITTER_BEARER_TOKEN") or None,
        reddit_client_id=os.environ.get("REDDIT_CLIENT_ID") or None,
        reddit_client_secret=os.environ.get("REDDIT_CLIENT_SECRET") or None,
        reddit_user_agent=os.environ.get("REDDIT_USER_AGENT", "memecoin-trader-bot/1.0"),
        birdeye_api_key=os.environ.get("BIRDEYE_API_KEY") or None,
        solana_private_key=os.environ.get("SOLANA_PRIVATE_KEY") or None,
        solana_rpc_url=os.environ.get("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com"),
        live_trading_confirmed=os.environ.get("I_UNDERSTAND_LIVE_TRADING_RISK", "").strip().lower()
        in ("yes", "true", "1"),
    )

    return Settings(
        mode=mode,
        starting_balance_usd=float(raw["portfolio"]["starting_balance_usd"]),
        chain_id=raw["chain"]["id"],
        timing=timing,
        entry=entry,
        exit=exit_cfg,
        paper_execution=paper_exec,
        live_execution=live_exec,
        mock_signal=mock_signal,
        twitter_signal=twitter_signal,
        scraper_signal=scraper_signal,
        reddit_signal=reddit_signal,
        pumpfun_signal=pumpfun_signal,
        birdeye_signal=birdeye_signal,
        secrets=secrets,
        raw=raw,
    )
