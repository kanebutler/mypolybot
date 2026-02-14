"""
Kelly Value Strategy - Edge-Based Trading for Prediction Markets

Uses the Kelly Criterion to identify and size positions on markets
where your probability estimate differs from the market price.

Unlike the flash-crash strategy which reacts to sudden drops, this
strategy continuously evaluates edge and places Kelly-sized bets.

Strategy Logic:
    1. Discover active 15-minute market
    2. Monitor orderbook in real-time via WebSocket
    3. For each tick, calculate Kelly sizing:
       F = fraction × (p - P) / (1 - P)
    4. If edge > min_edge and bet > min_bet → place order
    5. Manage positions with configurable TP/SL

Usage:
    from strategies.kelly_value import KellyValueStrategy, KellyValueConfig

    config = KellyValueConfig(
        coin="BTC",
        kelly_fraction=0.25,
        estimated_up_prob=0.55,  # Your model's estimate
    )
    strategy = KellyValueStrategy(bot, config)
    await strategy.run()
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Dict, Optional, Callable

from lib.console import Colors, format_countdown
from lib.price_tracker import PriceTracker
from lib.position_manager import PositionManager, Position
from src.bot import TradingBot, OrderResult
from src.kelly import KellyCriterion, KellySizing
from src.gamma_client import GammaClient
from src.websocket_client import MarketWebSocket, OrderbookSnapshot


@dataclass
class KellyValueConfig:
    """Configuration for the Kelly Value strategy."""

    coin: str = "ETH"

    # Kelly settings
    kelly_fraction: float = 0.25      # 1/4 Kelly
    min_edge: float = 0.03            # 3% minimum edge to trade
    max_bankroll_pct: float = 0.15    # Max 15% of bankroll per trade
    min_bet_usd: float = 2.0          # Minimum bet in USDC
    bankroll: float = 1000.0          # Starting bankroll

    # Probability estimates (your model's output)
    # Set these based on your analysis. If None, strategy won't trade that side.
    estimated_up_prob: Optional[float] = None
    estimated_down_prob: Optional[float] = None

    # Risk management
    take_profit: float = 0.10         # +10 cents TP
    stop_loss: float = 0.05           # -5 cents SL
    max_positions: int = 1
    cooldown_seconds: float = 30.0    # Min seconds between trades

    # Timing
    update_interval: float = 0.5
    market_check_interval: float = 30.0


class KellyValueStrategy:
    """
    Edge-based trading strategy using Kelly Criterion position sizing.

    The strategy:
    1. Compares your probability estimates to market prices
    2. Uses Kelly to determine optimal bet size
    3. Places orders only when edge exceeds threshold
    4. Manages positions with take-profit and stop-loss
    """

    def __init__(self, bot: TradingBot, config: KellyValueConfig):
        self.bot = bot
        self.config = config

        # Kelly calculator
        self.kelly = KellyCriterion(
            bankroll=config.bankroll,
            fraction=config.kelly_fraction,
            min_edge=config.min_edge,
            max_fraction=config.max_bankroll_pct,
            min_bet=config.min_bet_usd,
        )

        # Components
        self.gamma = GammaClient()
        self.ws: Optional[MarketWebSocket] = None
        self.prices = PriceTracker(lookback_seconds=10, max_history=100)
        self.positions = PositionManager(
            take_profit=config.take_profit,
            stop_loss=config.stop_loss,
            max_positions=config.max_positions,
        )

        # State
        self.running = False
        self.current_market = None
        self.token_ids: Dict[str, str] = {}
        self._last_trade_time: float = 0
        self._ws_connected = False

    async def run(self) -> None:
        """Main strategy loop."""
        self.running = True

        try:
            # Discover market
            print(f"{Colors.CYAN}Discovering {self.config.coin} 15-minute market...{Colors.RESET}")
            market_info = self.gamma.get_market_info(self.config.coin)
            if not market_info:
                print(f"{Colors.RED}No active market found for {self.config.coin}{Colors.RESET}")
                return

            self.current_market = market_info
            self.token_ids = market_info["token_ids"]
            print(f"{Colors.GREEN}Found: {market_info['question']}{Colors.RESET}")
            print(f"  UP token:   {self.token_ids.get('up', 'N/A')[:20]}...")
            print(f"  DOWN token: {self.token_ids.get('down', 'N/A')[:20]}...")

            # Setup WebSocket
            self.ws = MarketWebSocket()

            @self.ws.on_book
            async def on_book(snapshot: OrderbookSnapshot):
                for side, tid in self.token_ids.items():
                    if tid == snapshot.asset_id:
                        self.prices.record(side, snapshot.mid_price)

            @self.ws.on_connect
            def on_connect():
                self._ws_connected = True

            token_list = list(self.token_ids.values())
            await self.ws.subscribe(token_list, replace=True)

            # Run WS in background
            ws_task = asyncio.create_task(self.ws.run(auto_reconnect=True))

            # Wait for data
            for _ in range(50):
                if self._ws_connected and any(
                    self.ws.get_orderbook(tid) for tid in token_list
                ):
                    break
                await asyncio.sleep(0.1)

            print(f"\n{Colors.BOLD}{'='*80}{Colors.RESET}")
            print(f"{Colors.CYAN}Kelly Value Strategy Running{Colors.RESET}")
            print(f"  Bankroll: ${self.config.bankroll:.2f}")
            print(f"  Kelly fraction: {self.config.kelly_fraction:.0%}")
            print(f"  Min edge: {self.config.min_edge:.0%}")
            if self.config.estimated_up_prob:
                print(f"  Your UP estimate: {self.config.estimated_up_prob:.0%}")
            if self.config.estimated_down_prob:
                print(f"  Your DOWN estimate: {self.config.estimated_down_prob:.0%}")
            print(f"{Colors.BOLD}{'='*80}{Colors.RESET}\n")

            # Main loop
            while self.running:
                await self._tick()
                await asyncio.sleep(self.config.update_interval)

        except KeyboardInterrupt:
            print(f"\n{Colors.YELLOW}Stopped by user{Colors.RESET}")
        finally:
            self.running = False
            if self.ws:
                await self.ws.disconnect()
            self._print_summary()

    async def _tick(self) -> None:
        """Single strategy tick."""
        # Get current prices
        prices: Dict[str, float] = {}
        for side in ("up", "down"):
            tid = self.token_ids.get(side)
            if tid and self.ws:
                ob = self.ws.get_orderbook(tid)
                if ob:
                    prices[side] = ob.mid_price

        if not prices:
            return

        # Check exits first
        await self._check_exits(prices)

        # Evaluate Kelly opportunities
        if self.positions.can_open_position:
            await self._evaluate_opportunities(prices)

        # Render status
        self._render(prices)

    async def _evaluate_opportunities(self, prices: Dict[str, float]) -> None:
        """Evaluate trading opportunities using Kelly Criterion."""
        # Cooldown check
        if time.time() - self._last_trade_time < self.config.cooldown_seconds:
            return

        opportunities = []

        if self.config.estimated_up_prob and "up" in prices:
            sizing = self.kelly.calculate(self.config.estimated_up_prob, prices["up"])
            if sizing.has_edge and sizing.bet_size > 0:
                opportunities.append(("up", sizing))

        if self.config.estimated_down_prob and "down" in prices:
            sizing = self.kelly.calculate(self.config.estimated_down_prob, prices["down"])
            if sizing.has_edge and sizing.bet_size > 0:
                opportunities.append(("down", sizing))

        if not opportunities:
            return

        # Take the best opportunity (highest bet size = highest edge-adjusted value)
        opportunities.sort(key=lambda x: x[1].bet_size, reverse=True)
        side, sizing = opportunities[0]

        print(
            f"\n{Colors.MAGENTA}$ KELLY SIGNAL: {side.upper()}{Colors.RESET} | "
            f"Edge: {sizing.edge:.2%} | "
            f"Kelly F: {sizing.kelly_fraction:.4f} → {sizing.adjusted_fraction:.4f} | "
            f"Bet: ${sizing.bet_size:.2f} ({sizing.shares:.1f} shares @ {sizing.market_price:.4f})"
        )

        # Execute
        await self._execute_buy(side, sizing)

    async def _execute_buy(self, side: str, sizing: KellySizing) -> None:
        """Execute a Kelly-sized buy."""
        tid = self.token_ids.get(side)
        if not tid:
            return

        buy_price = min(sizing.market_price + 0.02, 0.99)

        result = await self.bot.place_order(
            token_id=tid, price=buy_price, size=sizing.shares, side="BUY",
        )

        if result.success:
            self._last_trade_time = time.time()
            self.positions.open_position(
                side=side, token_id=tid,
                entry_price=sizing.market_price,
                size=sizing.shares,
                order_id=result.order_id,
            )
            print(f"{Colors.GREEN}✓ Order placed: {result.order_id}{Colors.RESET}")
        else:
            print(f"{Colors.RED}✗ Order failed: {result.message}{Colors.RESET}")

    async def _check_exits(self, prices: Dict[str, float]) -> None:
        """Check TP/SL for all positions."""
        exits = self.positions.check_all_exits(prices)
        for position, exit_type, pnl in exits:
            if exit_type == "take_profit":
                print(f"{Colors.GREEN}✓ TAKE PROFIT: {position.side.upper()} +${pnl:.2f}{Colors.RESET}")
            else:
                print(f"{Colors.RED}✗ STOP LOSS: {position.side.upper()} ${pnl:.2f}{Colors.RESET}")

            # Sell
            tid = position.token_id
            sell_price = max(prices.get(position.side, 0) - 0.02, 0.01)
            result = await self.bot.place_order(
                token_id=tid, price=sell_price, size=position.size, side="SELL",
            )
            self.positions.close_position(position.id, realized_pnl=pnl)

            # Update Kelly bankroll
            self.kelly.update_bankroll(self.kelly.bankroll + pnl)

    def _render(self, prices: Dict[str, float]) -> None:
        """Render concise status line."""
        parts = []
        for side in ("up", "down"):
            p = prices.get(side, 0)
            if p > 0:
                color = Colors.GREEN if side == "up" else Colors.RED
                parts.append(f"{color}{side.upper()}: {p:.4f}{Colors.RESET}")

                # Show Kelly sizing
                est = (
                    self.config.estimated_up_prob if side == "up"
                    else self.config.estimated_down_prob
                )
                if est:
                    sizing = self.kelly.calculate(est, p)
                    edge_color = Colors.GREEN if sizing.edge > 0 else Colors.DIM
                    parts.append(f"{edge_color}edge={sizing.edge:+.2%}{Colors.RESET}")

        stats = self.positions.get_stats()
        parts.append(f"PnL: ${stats['total_pnl']:+.2f}")
        parts.append(f"BR: ${self.kelly.bankroll:.0f}")

        # Overwrite line
        print(f"\r{' | '.join(parts)}", end="", flush=True)

    def _print_summary(self) -> None:
        """Print session summary."""
        print(f"\n\n{Colors.BOLD}Session Summary{Colors.RESET}")
        stats = self.positions.get_stats()
        print(f"  Trades: {stats['trades_closed']}")
        print(f"  Total PnL: ${stats['total_pnl']:+.2f}")
        print(f"  Win rate: {stats['win_rate']:.1f}%")
        print(f"  Final bankroll: ${self.kelly.bankroll:.2f}")
