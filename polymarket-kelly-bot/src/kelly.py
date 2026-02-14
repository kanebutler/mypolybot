"""
Kelly Criterion Module - Position Sizing for Prediction Markets

Implements the Kelly Criterion adapted for prediction market pricing:

    F = (p - P) / (1 - P)

Where:
    F = fraction of bankroll to wager
    p = your estimated probability of the outcome
    P = market price (implied probability)

The numerator (p - P) is your edge.
The denominator (1 - P) is the potential profit per dollar risked.

Fractional Kelly is used in practice to reduce variance:
    - 1/2 Kelly: well-tested model
    - 1/4 Kelly: standard approach (default)
    - 1/8 Kelly: noisy / uncertain markets
    - < 1/8 Kelly: tail risk present

Example:
    from src.kelly import KellyCriterion

    kelly = KellyCriterion(bankroll=1000.0, fraction=0.25)

    sizing = kelly.calculate(
        estimated_prob=0.60,
        market_price=0.40
    )

    print(f"Bet: ${sizing.bet_size:.2f}")
    print(f"Edge: {sizing.edge:.2%}")
    print(f"Kelly fraction: {sizing.kelly_fraction:.4f}")
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class KellySizing:
    """Result of a Kelly Criterion calculation."""

    kelly_fraction: float      # Raw full-Kelly fraction (0–1)
    adjusted_fraction: float   # After applying fractional multiplier
    bet_size: float            # Dollar amount to bet
    edge: float                # p - P (your edge over the market)
    potential_profit: float    # 1 - P (profit if you win per $1 risked)
    estimated_prob: float      # Your probability estimate
    market_price: float        # Market-implied probability
    bankroll: float            # Current bankroll
    fraction_used: float       # Fractional Kelly multiplier applied

    @property
    def has_edge(self) -> bool:
        """True when your estimate exceeds the market price."""
        return self.edge > 0

    @property
    def expected_value(self) -> float:
        """Expected value per dollar risked."""
        if self.market_price >= 1:
            return 0.0
        odds = 1.0 / self.market_price
        return (odds * self.estimated_prob) - 1.0

    @property
    def implied_odds(self) -> float:
        """Market price expressed as decimal odds."""
        if self.market_price <= 0:
            return float("inf")
        return 1.0 / self.market_price

    @property
    def shares(self) -> float:
        """Number of shares the bet size buys at the market price."""
        if self.market_price <= 0:
            return 0.0
        return self.bet_size / self.market_price

    def summary(self) -> str:
        """Human-readable summary."""
        if not self.has_edge:
            return (
                f"No edge: your p={self.estimated_prob:.2%} "
                f"<= market P={self.market_price:.2%}"
            )
        return (
            f"Edge: {self.edge:.2%} | "
            f"Kelly: {self.kelly_fraction:.4f} | "
            f"Adjusted ({self.fraction_used:.0%}): {self.adjusted_fraction:.4f} | "
            f"Bet: ${self.bet_size:.2f} / ${self.bankroll:.2f} bankroll | "
            f"Shares: {self.shares:.1f} @ {self.market_price:.2f}"
        )


class KellyCriterion:
    """
    Kelly Criterion calculator for prediction markets.

    The prediction-market form:
        F = (p - P) / (1 - P)

    With fractional Kelly for practical use:
        F_adj = fraction × F

    Attributes:
        bankroll: Total capital available for trading
        fraction: Fractional Kelly multiplier (0.25 = quarter-Kelly)
        min_edge: Minimum edge required to generate a signal (default 0.01)
        max_fraction: Cap on the adjusted fraction (default 0.20)
        min_bet: Minimum bet size in dollars (default 1.0)
    """

    # Preset fractional Kelly levels
    AGGRESSIVE = 0.50    # 1/2 Kelly — well-tested model
    STANDARD = 0.25      # 1/4 Kelly — default
    CONSERVATIVE = 0.125 # 1/8 Kelly — noisy markets
    CAUTIOUS = 0.0625    # 1/16 Kelly — tail risk

    def __init__(
        self,
        bankroll: float = 1000.0,
        fraction: float = 0.25,
        min_edge: float = 0.01,
        max_fraction: float = 0.20,
        min_bet: float = 1.0,
    ):
        if bankroll < 0:
            raise ValueError("Bankroll cannot be negative")
        if not 0 < fraction <= 1:
            raise ValueError("Fraction must be in (0, 1]")
        if max_fraction <= 0:
            raise ValueError("max_fraction must be positive")

        self.bankroll = bankroll
        self.fraction = fraction
        self.min_edge = min_edge
        self.max_fraction = max_fraction
        self.min_bet = min_bet

    def calculate(
        self,
        estimated_prob: float,
        market_price: float,
        bankroll: Optional[float] = None,
    ) -> KellySizing:
        """
        Calculate Kelly-optimal position size.

        Args:
            estimated_prob: Your probability estimate (0–1)
            market_price: Current market price / implied probability (0–1)
            bankroll: Override bankroll for this calculation

        Returns:
            KellySizing with all computed values
        """
        br = bankroll if bankroll is not None else self.bankroll

        # Validate inputs
        estimated_prob = max(0.0, min(1.0, estimated_prob))
        market_price = max(0.001, min(0.999, market_price))

        edge = estimated_prob - market_price
        potential_profit = 1.0 - market_price

        # Full Kelly fraction: F = edge / potential_profit
        if potential_profit > 0:
            kelly_f = edge / potential_profit
        else:
            kelly_f = 0.0

        # Clamp: no negative bets, no over-betting
        kelly_f = max(0.0, kelly_f)

        # Apply fractional Kelly
        adjusted = kelly_f * self.fraction

        # Cap at max_fraction
        adjusted = min(adjusted, self.max_fraction)

        # Compute dollar bet
        bet = adjusted * br

        # Enforce minimum edge
        if edge < self.min_edge:
            kelly_f = 0.0
            adjusted = 0.0
            bet = 0.0

        # Enforce minimum bet
        if 0 < bet < self.min_bet:
            bet = 0.0
            adjusted = 0.0

        return KellySizing(
            kelly_fraction=kelly_f,
            adjusted_fraction=adjusted,
            bet_size=bet,
            edge=edge,
            potential_profit=potential_profit,
            estimated_prob=estimated_prob,
            market_price=market_price,
            bankroll=br,
            fraction_used=self.fraction,
        )

    def calculate_for_odds(
        self,
        estimated_prob: float,
        odds: float,
        bankroll: Optional[float] = None,
    ) -> KellySizing:
        """
        Calculate using traditional decimal odds instead of market price.

        The original Kelly formula:
            F = (K × p - 1) / (K - 1)

        Where K = decimal odds, p = your estimated probability.

        Since K = 1 / P, this is equivalent to the prediction-market form.

        Args:
            estimated_prob: Your probability estimate
            odds: Decimal odds (e.g. 4.0 means 4x return)
            bankroll: Override bankroll

        Returns:
            KellySizing
        """
        if odds <= 0:
            raise ValueError("Odds must be positive")
        market_price = 1.0 / odds
        return self.calculate(estimated_prob, market_price, bankroll)

    def update_bankroll(self, new_bankroll: float) -> None:
        """Update the bankroll after wins/losses."""
        if new_bankroll < 0:
            raise ValueError("Bankroll cannot be negative")
        self.bankroll = new_bankroll

    def batch_calculate(
        self,
        opportunities: list[tuple[float, float]],
        bankroll: Optional[float] = None,
    ) -> list[KellySizing]:
        """
        Calculate Kelly sizing for multiple opportunities.

        Args:
            opportunities: List of (estimated_prob, market_price) tuples
            bankroll: Override bankroll

        Returns:
            List of KellySizing results
        """
        return [
            self.calculate(p, mp, bankroll)
            for p, mp in opportunities
        ]

    def rank_opportunities(
        self,
        opportunities: list[tuple[str, float, float]],
        bankroll: Optional[float] = None,
    ) -> list[tuple[str, KellySizing]]:
        """
        Rank trading opportunities by Kelly-optimal bet size.

        Args:
            opportunities: List of (label, estimated_prob, market_price)
            bankroll: Override bankroll

        Returns:
            List of (label, KellySizing) sorted by bet_size descending
        """
        results = [
            (label, self.calculate(p, mp, bankroll))
            for label, p, mp in opportunities
        ]
        results.sort(key=lambda x: x[1].bet_size, reverse=True)
        return results

    def __repr__(self) -> str:
        return (
            f"KellyCriterion(bankroll=${self.bankroll:.2f}, "
            f"fraction={self.fraction:.2%}, "
            f"min_edge={self.min_edge:.2%})"
        )
