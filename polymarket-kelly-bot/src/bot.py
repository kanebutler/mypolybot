"""
Trading Bot - Main Interface with Kelly Criterion Position Sizing

The bot integrates Kelly Criterion to determine optimal position sizes
based on your edge over the market.

Example:
    bot = TradingBot(config=config, private_key="0x...")

    # Kelly-sized order: provide your probability estimate
    result = await bot.place_kelly_order(
        token_id="123...",
        estimated_prob=0.60,  # You think 60%
        market_price=0.40,    # Market says 40%
        side="BUY",
    )

    # Manual order (bypass Kelly)
    result = await bot.place_order(token_id="123...", price=0.65, size=10, side="BUY")
"""

import os
import asyncio
import logging
from typing import Optional, Dict, Any, List, Callable, TypeVar
from dataclasses import dataclass, field
from enum import Enum

from .config import Config, BuilderConfig
from .signer import OrderSigner, Order
from .client import ClobClient, RelayerClient, ApiCredentials
from .crypto import KeyManager, CryptoError, InvalidPasswordError
from .kelly import KellyCriterion, KellySizing

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)
T = TypeVar("T")


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass
class OrderResult:
    """Result of an order operation."""
    success: bool
    order_id: Optional[str] = None
    status: Optional[str] = None
    message: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    kelly_sizing: Optional[KellySizing] = None

    @classmethod
    def from_response(cls, response: Dict[str, Any], kelly: Optional[KellySizing] = None) -> "OrderResult":
        success = response.get("success", False)
        return cls(
            success=success,
            order_id=response.get("orderId"),
            status=response.get("status"),
            message=response.get("errorMsg", "") if not success else "Order placed",
            data=response,
            kelly_sizing=kelly,
        )


class TradingBotError(Exception):
    pass

class NotInitializedError(TradingBotError):
    pass


class TradingBot:
    """
    Polymarket trading bot with Kelly Criterion position sizing.

    Kelly integration:
    - Automatically sizes positions based on your edge
    - Configurable fractional Kelly (1/4 Kelly default)
    - Tracks bankroll for dynamic sizing
    """

    def __init__(
        self,
        config_path: Optional[str] = None,
        config: Optional[Config] = None,
        safe_address: Optional[str] = None,
        builder_creds: Optional[BuilderConfig] = None,
        private_key: Optional[str] = None,
        encrypted_key_path: Optional[str] = None,
        password: Optional[str] = None,
        bankroll: Optional[float] = None,
        log_level: int = logging.INFO,
    ):
        logger.setLevel(log_level)

        if config_path:
            self.config = Config.load(config_path)
        elif config:
            self.config = config
        else:
            self.config = Config()

        if safe_address:
            self.config.safe_address = safe_address
        if builder_creds:
            self.config.builder = builder_creds
            self.config.use_gasless = True

        # Initialize components
        self.signer: Optional[OrderSigner] = None
        self.clob_client: Optional[ClobClient] = None
        self.relayer_client: Optional[RelayerClient] = None
        self._api_creds: Optional[ApiCredentials] = None

        # Kelly Criterion
        br = bankroll or 1000.0
        self.kelly = KellyCriterion(
            bankroll=br,
            fraction=self.config.kelly.fraction,
            min_edge=self.config.kelly.min_edge,
            max_fraction=self.config.kelly.max_fraction,
            min_bet=self.config.kelly.min_bet,
        )

        # Load keys
        if private_key:
            self.signer = OrderSigner(private_key)
        elif encrypted_key_path and password:
            self._load_encrypted_key(encrypted_key_path, password)

        self._init_clients()

        if self.signer and not self._api_creds:
            self._derive_api_creds()

        logger.info(f"TradingBot initialized (gasless={self.config.use_gasless}, kelly={self.kelly})")

    def _load_encrypted_key(self, filepath: str, password: str) -> None:
        try:
            manager = KeyManager()
            pk = manager.load_and_decrypt(password, filepath)
            self.signer = OrderSigner(pk)
        except FileNotFoundError:
            raise TradingBotError(f"Key file not found: {filepath}")
        except InvalidPasswordError:
            raise TradingBotError("Invalid password")
        except CryptoError as e:
            raise TradingBotError(f"Crypto error: {e}")

    def _derive_api_creds(self) -> None:
        if not self.signer or not self.clob_client:
            return
        try:
            self._api_creds = self.clob_client.create_or_derive_api_key(self.signer)
            self.clob_client.set_api_creds(self._api_creds)
            logger.info("API credentials derived")
        except Exception as e:
            logger.warning(f"Failed to derive API creds: {e}")

    def _init_clients(self) -> None:
        self.clob_client = ClobClient(
            host=self.config.clob.host,
            chain_id=self.config.clob.chain_id,
            signature_type=self.config.clob.signature_type,
            funder=self.config.safe_address,
            api_creds=self._api_creds,
            builder_creds=self.config.builder if self.config.use_gasless else None,
        )
        if self.config.use_gasless:
            self.relayer_client = RelayerClient(
                host=self.config.relayer.host,
                chain_id=self.config.clob.chain_id,
                builder_creds=self.config.builder,
                tx_type=self.config.relayer.tx_type,
            )

    async def _run_in_thread(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        return await asyncio.to_thread(func, *args, **kwargs)

    def is_initialized(self) -> bool:
        return self.signer is not None and bool(self.config.safe_address) and self.clob_client is not None

    def require_signer(self) -> OrderSigner:
        if not self.signer:
            raise NotInitializedError("Signer not initialized")
        return self.signer

    # ─── Kelly-Sized Orders ────────────────────────────────────────────

    def get_kelly_sizing(self, estimated_prob: float, market_price: float) -> KellySizing:
        """
        Calculate Kelly-optimal position size without placing an order.

        Args:
            estimated_prob: Your probability estimate (0-1)
            market_price: Current market price (0-1)

        Returns:
            KellySizing with bet size, edge, and Kelly fraction
        """
        return self.kelly.calculate(estimated_prob, market_price)

    async def place_kelly_order(
        self,
        token_id: str,
        estimated_prob: float,
        market_price: float,
        side: str = "BUY",
        order_type: str = "GTC",
    ) -> OrderResult:
        """
        Place an order sized by the Kelly Criterion.

        The bet size is determined by:
            F = fraction × (p - P) / (1 - P)
            bet = F × bankroll

        Args:
            token_id: Market token ID
            estimated_prob: Your probability estimate
            market_price: Current market price
            side: BUY or SELL
            order_type: GTC, GTD, FOK

        Returns:
            OrderResult with Kelly sizing details
        """
        sizing = self.kelly.calculate(estimated_prob, market_price)

        if not sizing.has_edge:
            logger.info(f"No edge: p={estimated_prob:.2%} <= P={market_price:.2%}")
            return OrderResult(
                success=False,
                message=f"No edge: your p={estimated_prob:.2%} <= market P={market_price:.2%}",
                kelly_sizing=sizing,
            )

        if sizing.bet_size <= 0:
            logger.info(f"Bet too small: ${sizing.bet_size:.2f}")
            return OrderResult(
                success=False,
                message=f"Bet size below minimum (edge={sizing.edge:.2%})",
                kelly_sizing=sizing,
            )

        shares = sizing.shares
        logger.info(
            f"Kelly order: {side} {shares:.1f} shares @ {market_price:.4f} "
            f"(${sizing.bet_size:.2f}, edge={sizing.edge:.2%}, "
            f"kelly={sizing.kelly_fraction:.4f}×{sizing.fraction_used:.0%})"
        )

        result = await self.place_order(
            token_id=token_id,
            price=market_price,
            size=shares,
            side=side,
            order_type=order_type,
        )
        result.kelly_sizing = sizing
        return result

    # ─── Standard Orders ───────────────────────────────────────────────

    async def place_order(
        self, token_id: str, price: float, size: float, side: str,
        order_type: str = "GTC", fee_rate_bps: int = 0,
    ) -> OrderResult:
        """Place a limit order (manual sizing)."""
        signer = self.require_signer()
        try:
            order = Order(
                token_id=token_id, price=price, size=size, side=side,
                maker=self.config.safe_address, fee_rate_bps=fee_rate_bps,
            )
            signed = signer.sign_order(order)
            response = await self._run_in_thread(self.clob_client.post_order, signed, order_type)
            logger.info(f"Order: {side} {size}@{price} (token: {token_id[:16]}...)")
            return OrderResult.from_response(response)
        except Exception as e:
            logger.error(f"Order failed: {e}")
            return OrderResult(success=False, message=str(e))

    async def cancel_order(self, order_id: str) -> OrderResult:
        try:
            resp = await self._run_in_thread(self.clob_client.cancel_order, order_id)
            return OrderResult(success=True, order_id=order_id, message="Cancelled", data=resp)
        except Exception as e:
            return OrderResult(success=False, order_id=order_id, message=str(e))

    async def cancel_all_orders(self) -> OrderResult:
        try:
            resp = await self._run_in_thread(self.clob_client.cancel_all_orders)
            return OrderResult(success=True, message="All cancelled", data=resp)
        except Exception as e:
            return OrderResult(success=False, message=str(e))

    async def get_open_orders(self) -> List[Dict[str, Any]]:
        try:
            return await self._run_in_thread(self.clob_client.get_open_orders)
        except Exception:
            return []

    async def get_trades(self, token_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        try:
            return await self._run_in_thread(self.clob_client.get_trades, token_id, limit)
        except Exception:
            return []

    async def get_order_book(self, token_id: str) -> Dict[str, Any]:
        try:
            return await self._run_in_thread(self.clob_client.get_order_book, token_id)
        except Exception:
            return {}

    async def get_market_price(self, token_id: str) -> Dict[str, Any]:
        try:
            return await self._run_in_thread(self.clob_client.get_market_price, token_id)
        except Exception:
            return {}


def create_bot_from_env(bankroll: float = 1000.0) -> TradingBot:
    """Create a TradingBot from environment variables."""
    pk = os.environ.get("POLY_PRIVATE_KEY", "")
    if not pk:
        raise ValueError("POLY_PRIVATE_KEY required")
    sa = os.environ.get("POLY_SAFE_ADDRESS", "")
    if not sa:
        raise ValueError("POLY_SAFE_ADDRESS required")
    config = Config.from_env()
    return TradingBot(config=config, private_key=pk, bankroll=bankroll)
