"""
WebSocket Client - Real-time orderbook and price data from Polymarket.

Example:
    ws = MarketWebSocket()
    @ws.on_book
    async def on_update(snapshot):
        print(f"Mid: {snapshot.mid_price}")
    await ws.subscribe(["token_id"])
    await ws.run()
"""

import json
import asyncio
import logging
from typing import Optional, Dict, Any, List, Callable, Set, Union, Awaitable, TYPE_CHECKING
from dataclasses import dataclass, field

if TYPE_CHECKING:
    from websockets.client import WebSocketClientProtocol

logger = logging.getLogger(__name__)

WSS_MARKET_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


def _load_websockets():
    try:
        from websockets.asyncio.client import connect as ws_connect
        from websockets.exceptions import ConnectionClosed
        return ws_connect, ConnectionClosed
    except ImportError:
        try:
            import websockets
            return websockets.connect, websockets.exceptions.ConnectionClosed
        except ImportError:
            return None, Exception


@dataclass
class OrderbookLevel:
    price: float
    size: float


@dataclass
class OrderbookSnapshot:
    asset_id: str
    market: str
    timestamp: int
    bids: List[OrderbookLevel] = field(default_factory=list)
    asks: List[OrderbookLevel] = field(default_factory=list)
    hash: str = ""

    @property
    def best_bid(self) -> float:
        return self.bids[0].price if self.bids else 0.0

    @property
    def best_ask(self) -> float:
        return self.asks[0].price if self.asks else 1.0

    @property
    def mid_price(self) -> float:
        if self.best_bid > 0 and self.best_ask < 1:
            return (self.best_bid + self.best_ask) / 2
        return self.best_bid or self.best_ask or 0.5

    @classmethod
    def from_message(cls, msg: Dict[str, Any]) -> "OrderbookSnapshot":
        bids = sorted(
            [OrderbookLevel(float(b["price"]), float(b["size"])) for b in msg.get("bids", [])],
            key=lambda x: x.price, reverse=True,
        )
        asks = sorted(
            [OrderbookLevel(float(a["price"]), float(a["size"])) for a in msg.get("asks", [])],
            key=lambda x: x.price,
        )
        return cls(
            asset_id=msg.get("asset_id", ""), market=msg.get("market", ""),
            timestamp=int(msg.get("timestamp", 0)), bids=bids, asks=asks,
            hash=msg.get("hash", ""),
        )


BookCallback = Callable[[OrderbookSnapshot], Union[None, Awaitable[None]]]


class MarketWebSocket:
    """WebSocket client for Polymarket market data."""

    def __init__(self, url: str = WSS_MARKET_URL, reconnect_interval: float = 5.0,
                 ping_interval: float = 20.0, ping_timeout: float = 10.0):
        self.url = url
        self.reconnect_interval = reconnect_interval
        self.ping_interval = ping_interval
        self.ping_timeout = ping_timeout
        self._ws_connect, self._connection_closed = _load_websockets()
        self._ws: Optional["WebSocketClientProtocol"] = None
        self._running = False
        self._subscribed_assets: Set[str] = set()
        self._orderbooks: Dict[str, OrderbookSnapshot] = {}
        self._on_book: Optional[BookCallback] = None
        self._on_connect: Optional[Callable[[], None]] = None
        self._on_disconnect: Optional[Callable[[], None]] = None

    @property
    def is_connected(self) -> bool:
        if self._ws is None:
            return False
        try:
            from websockets.protocol import State
            return self._ws.state == State.OPEN
        except (ImportError, AttributeError):
            try:
                return self._ws.open
            except AttributeError:
                return False

    def on_book(self, callback: BookCallback) -> BookCallback:
        self._on_book = callback
        return callback

    def on_connect(self, callback: Callable) -> Callable:
        self._on_connect = callback
        return callback

    def on_disconnect(self, callback: Callable) -> Callable:
        self._on_disconnect = callback
        return callback

    def get_orderbook(self, asset_id: str) -> Optional[OrderbookSnapshot]:
        return self._orderbooks.get(asset_id)

    def get_mid_price(self, asset_id: str) -> float:
        ob = self._orderbooks.get(asset_id)
        return ob.mid_price if ob else 0.0

    async def connect(self) -> bool:
        if not self._ws_connect:
            logger.error("websockets library not available")
            return False
        try:
            self._ws = await self._ws_connect(
                self.url, ping_interval=self.ping_interval, ping_timeout=self.ping_timeout,
            )
            if self._on_connect:
                self._on_connect()
            return True
        except Exception as e:
            logger.error(f"WS connect failed: {e}")
            return False

    async def disconnect(self) -> None:
        self._running = False
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    async def subscribe(self, asset_ids: List[str], replace: bool = False) -> bool:
        if replace:
            self._subscribed_assets = set(asset_ids)
        else:
            self._subscribed_assets.update(asset_ids)
        if not self.is_connected:
            return True  # Will subscribe on connect
        msg = {"assets_ids": asset_ids, "type": "market"}
        try:
            await self._ws.send(json.dumps(msg))
            return True
        except Exception as e:
            logger.error(f"Subscribe failed: {e}")
            return False

    async def _handle_message(self, data: Dict[str, Any]) -> None:
        event_type = data.get("event_type", "")
        if event_type == "book":
            snapshot = OrderbookSnapshot.from_message(data)
            self._orderbooks[snapshot.asset_id] = snapshot
            if self._on_book:
                result = self._on_book(snapshot)
                if asyncio.iscoroutine(result):
                    await result

    async def _run_loop(self) -> None:
        while self._running and self.is_connected:
            try:
                message = await asyncio.wait_for(self._ws.recv(), timeout=self.ping_interval + 5)
                data = json.loads(message)
                if isinstance(data, list):
                    for item in data:
                        await self._handle_message(item)
                else:
                    await self._handle_message(data)
            except asyncio.TimeoutError:
                pass
            except self._connection_closed:
                break
            except Exception as e:
                logger.error(f"WS error: {e}")

    async def run(self, auto_reconnect: bool = True) -> None:
        self._running = True
        while self._running:
            if not await self.connect():
                if auto_reconnect:
                    await asyncio.sleep(self.reconnect_interval)
                    continue
                break
            if self._subscribed_assets:
                await self.subscribe(list(self._subscribed_assets))
            await self._run_loop()
            if self._on_disconnect:
                self._on_disconnect()
            if not self._running or not auto_reconnect:
                break
            await asyncio.sleep(self.reconnect_interval)

    def stop(self) -> None:
        self._running = False
