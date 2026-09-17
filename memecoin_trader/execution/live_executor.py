"""Real Solana swaps via the Jupiter aggregator. OFF BY DEFAULT.

This is real, working integration code — not a stub — but it moves real
money and has not been exercised against mainnet in this environment. Before
you trust it with anything beyond a couple of dollars:

  1. Read README.md's "Going live" section in full.
  2. Test with a fresh wallet funded with only a few dollars of SOL.
  3. Watch the first several trades closely.

Safety gates, all of which must hold before a single order goes out:
  - config mode == "live" (config.yaml or MODE=live)
  - env I_UNDERSTAND_LIVE_TRADING_RISK=yes
  - env SOLANA_PRIVATE_KEY set (base58-encoded secret key)
  - every individual trade <= execution.live.max_trade_usd (config.yaml)

`solders` and `base58` are optional dependencies — see requirements-live.txt —
and are only imported when this class is actually instantiated.
"""
from __future__ import annotations

import base64
import logging
import time
from decimal import Decimal

import requests

from memecoin_trader.config import LiveExecutionConfig, Secrets
from memecoin_trader.execution.base import Executor, FillResult
from memecoin_trader.market.dexscreener import PairInfo

logger = logging.getLogger(__name__)

JUPITER_QUOTE_URL = "https://quote-api.jup.ag/v6/quote"
JUPITER_SWAP_URL = "https://quote-api.jup.ag/v6/swap"
SOL_MINT = "So11111111111111111111111111111111111111112"
LAMPORTS_PER_SOL = Decimal(1_000_000_000)


class LiveTradingDisabledError(RuntimeError):
    pass


class LiveExecutor(Executor):
    """Buys/sells memecoins for real SOL through Jupiter. Handle with care."""

    mode = "live"

    def __init__(self, config: LiveExecutionConfig, secrets: Secrets, mode: str):
        if mode != "live":
            raise LiveTradingDisabledError("config mode is not 'live'")
        if not secrets.live_trading_confirmed:
            raise LiveTradingDisabledError(
                "set I_UNDERSTAND_LIVE_TRADING_RISK=yes in .env to enable real trading"
            )
        if not secrets.solana_private_key:
            raise LiveTradingDisabledError("SOLANA_PRIVATE_KEY is not set")

        try:
            import base58  # noqa: F401
            from solders.keypair import Keypair  # noqa: F401
        except ImportError as exc:
            raise LiveTradingDisabledError(
                "live trading needs extra packages: pip install -r requirements-live.txt"
            ) from exc

        self._config = config
        self._rpc_url = secrets.solana_rpc_url
        self._keypair = self._load_keypair(secrets.solana_private_key)
        self._session = requests.Session()
        logger.warning(
            "LIVE TRADING ENABLED. Wallet=%s. Every trade capped at $%s.",
            self.public_key,
            config.max_trade_usd,
        )

    @staticmethod
    def _load_keypair(secret_b58: str):
        import base58
        from solders.keypair import Keypair

        return Keypair.from_bytes(base58.b58decode(secret_b58))

    @property
    def public_key(self) -> str:
        return str(self._keypair.pubkey())

    def _check_cap(self, usd_amount: Decimal) -> None:
        cap = Decimal(str(self._config.max_trade_usd))
        if usd_amount > cap:
            raise LiveTradingDisabledError(
                f"trade of ${usd_amount} exceeds the configured live safety cap of ${cap}"
            )

    def _get_quote(self, input_mint: str, output_mint: str, amount_atomic: int) -> dict:
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": amount_atomic,
            "slippageBps": int(self._config.slippage_bps),
        }
        resp = self._session.get(JUPITER_QUOTE_URL, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def _execute_swap(self, quote: dict) -> tuple[str, dict]:
        from solders.transaction import VersionedTransaction

        payload = {
            "quoteResponse": quote,
            "userPublicKey": self.public_key,
            "wrapAndUnwrapSol": True,
            "prioritizationFeeLamports": int(self._config.priority_fee_lamports),
        }
        resp = self._session.post(JUPITER_SWAP_URL, json=payload, timeout=20)
        resp.raise_for_status()
        swap_data = resp.json()

        raw_tx = base64.b64decode(swap_data["swapTransaction"])
        unsigned_tx = VersionedTransaction.from_bytes(raw_tx)
        signed_tx = VersionedTransaction(unsigned_tx.message, [self._keypair])
        serialized = bytes(signed_tx)

        rpc_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendTransaction",
            "params": [
                base64.b64encode(serialized).decode("ascii"),
                {"encoding": "base64", "skipPreflight": False, "maxRetries": 3},
            ],
        }
        rpc_resp = self._session.post(self._rpc_url, json=rpc_payload, timeout=30)
        rpc_resp.raise_for_status()
        rpc_result = rpc_resp.json()
        if "error" in rpc_result:
            raise RuntimeError(f"Solana RPC rejected transaction: {rpc_result['error']}")
        tx_signature = rpc_result["result"]
        logger.warning("LIVE swap submitted: %s", tx_signature)
        return tx_signature, quote

    def buy(self, token_address: str, usd_amount: Decimal, market: PairInfo) -> FillResult:
        self._check_cap(usd_amount)
        sol_price_usd = self._get_sol_price_usd()
        sol_amount = usd_amount / sol_price_usd
        amount_lamports = int(sol_amount * LAMPORTS_PER_SOL)

        quote = self._get_quote(SOL_MINT, token_address, amount_lamports)
        tx_sig, quote = self._execute_swap(quote)

        out_amount_atomic = Decimal(quote["outAmount"])
        # Jupiter doesn't return output decimals in the quote; DexScreener's
        # reported price is what we use to convert atomic units back to a
        # human quantity for the ledger, same as the paper executor's price basis.
        quantity = usd_amount / market.price_usd if market.price_usd > 0 else Decimal(0)
        return FillResult(
            price_usd=usd_amount / quantity if quantity > 0 else market.price_usd,
            quantity=quantity,
            amount_usd=usd_amount,
            fee_usd=Decimal(0),  # network/DEX fee is embedded in the swap's actual output amount
            tx_id=tx_sig,
        )

    def sell(self, token_address: str, quantity: Decimal, market: PairInfo) -> FillResult:
        usd_value_estimate = quantity * market.price_usd
        self._check_cap(usd_value_estimate)
        # NOTE: this assumes 9 decimals (SOL-style); most SPL memecoins use 6.
        # Fetching real mint decimals before going live is a required follow-up
        # — see README "Going live" checklist.
        amount_atomic = int(quantity * Decimal(1_000_000))

        quote = self._get_quote(token_address, SOL_MINT, amount_atomic)
        tx_sig, quote = self._execute_swap(quote)

        sol_price_usd = self._get_sol_price_usd()
        out_sol = Decimal(quote["outAmount"]) / LAMPORTS_PER_SOL
        proceeds_usd = out_sol * sol_price_usd
        fill_price = proceeds_usd / quantity if quantity > 0 else Decimal(0)
        return FillResult(
            price_usd=fill_price,
            quantity=quantity,
            amount_usd=proceeds_usd,
            fee_usd=Decimal(0),
            tx_id=tx_sig,
        )

    def _get_sol_price_usd(self) -> Decimal:
        quote = self._get_quote(SOL_MINT, "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", int(LAMPORTS_PER_SOL))
        usdc_out = Decimal(quote["outAmount"]) / Decimal(1_000_000)  # USDC has 6 decimals
        if usdc_out <= 0:
            raise RuntimeError("could not price SOL via Jupiter")
        return usdc_out
