"""
Gamma API Client - Market Discovery for Polymarket

Discovers active 15-minute Up/Down markets for crypto assets.

Example:
    from src.gamma_client import GammaClient
    client = GammaClient()
    market = client.get_market_info("BTC")
"""

import json
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
from .http import ThreadLocalSessionMixin


class GammaClient(ThreadLocalSessionMixin):
    """Client for Polymarket's Gamma API."""

    DEFAULT_HOST = "https://gamma-api.polymarket.com"
    COIN_SLUGS = {
        "BTC": "btc-updown-15m",
        "ETH": "eth-updown-15m",
        "SOL": "sol-updown-15m",
        "XRP": "xrp-updown-15m",
    }

    def __init__(self, host: str = DEFAULT_HOST, timeout: int = 10):
        super().__init__()
        self.host = host.rstrip("/")
        self.timeout = timeout

    def get_market_by_slug(self, slug: str) -> Optional[Dict[str, Any]]:
        try:
            r = self.session.get(f"{self.host}/markets/slug/{slug}", timeout=self.timeout)
            return r.json() if r.status_code == 200 else None
        except Exception:
            return None

    def get_current_15m_market(self, coin: str) -> Optional[Dict[str, Any]]:
        coin = coin.upper()
        if coin not in self.COIN_SLUGS:
            raise ValueError(f"Unsupported coin: {coin}. Use: {list(self.COIN_SLUGS.keys())}")

        prefix = self.COIN_SLUGS[coin]
        now = datetime.now(timezone.utc)
        minute = (now.minute // 15) * 15
        current_window = now.replace(minute=minute, second=0, microsecond=0)
        current_ts = int(current_window.timestamp())

        for offset in [0, 900, -900]:
            slug = f"{prefix}-{current_ts + offset}"
            market = self.get_market_by_slug(slug)
            if market and market.get("acceptingOrders"):
                return market
        return None

    def parse_token_ids(self, market: Dict[str, Any]) -> Dict[str, str]:
        token_ids = self._parse_json(market.get("clobTokenIds", "[]"))
        outcomes = self._parse_json(market.get("outcomes", '["Up", "Down"]'))
        return self._map_outcomes(outcomes, token_ids)

    def parse_prices(self, market: Dict[str, Any]) -> Dict[str, float]:
        prices = self._parse_json(market.get("outcomePrices", '["0.5", "0.5"]'))
        outcomes = self._parse_json(market.get("outcomes", '["Up", "Down"]'))
        return self._map_outcomes(outcomes, prices, cast=float)

    @staticmethod
    def _parse_json(value) -> List[Any]:
        return json.loads(value) if isinstance(value, str) else value

    @staticmethod
    def _map_outcomes(outcomes, values, cast=lambda v: v) -> Dict[str, Any]:
        return {
            str(outcomes[i]).lower(): cast(values[i])
            for i in range(min(len(outcomes), len(values)))
        }

    def get_market_info(self, coin: str) -> Optional[Dict[str, Any]]:
        market = self.get_current_15m_market(coin)
        if not market:
            return None
        return {
            "slug": market.get("slug"),
            "question": market.get("question"),
            "end_date": market.get("endDate"),
            "token_ids": self.parse_token_ids(market),
            "prices": self.parse_prices(market),
            "accepting_orders": market.get("acceptingOrders", False),
            "raw": market,
        }
