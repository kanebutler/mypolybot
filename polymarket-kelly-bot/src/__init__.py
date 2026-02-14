"""
Polymarket Kelly Bot - Trading Bot with Kelly Criterion Position Sizing

A Polymarket trading bot that uses the Kelly Criterion to determine
optimal position sizes based on your edge over the market.

Quick Start:
    from src import create_bot_from_env
    bot = create_bot_from_env(bankroll=1000)

    # Kelly-sized order
    result = await bot.place_kelly_order(
        token_id="...", estimated_prob=0.60, market_price=0.40
    )

    # Or use Kelly standalone
    from src.kelly import KellyCriterion
    kelly = KellyCriterion(bankroll=1000, fraction=0.25)
    sizing = kelly.calculate(estimated_prob=0.60, market_price=0.40)
"""

from .bot import TradingBot, OrderResult, create_bot_from_env
from .kelly import KellyCriterion, KellySizing
from .signer import OrderSigner, Order
from .client import ClobClient, RelayerClient
from .crypto import KeyManager
from .config import Config, BuilderConfig, KellyConfig
from .gamma_client import GammaClient
from .websocket_client import MarketWebSocket, OrderbookSnapshot

__version__ = "1.0.0"

__all__ = [
    "TradingBot", "OrderResult", "create_bot_from_env",
    "KellyCriterion", "KellySizing",
    "OrderSigner", "Order",
    "ClobClient", "RelayerClient",
    "KeyManager",
    "Config", "BuilderConfig", "KellyConfig",
    "GammaClient",
    "MarketWebSocket", "OrderbookSnapshot",
]
