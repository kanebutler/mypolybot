"""
Tests for the Kelly Criterion module.

Covers:
- Basic formula: F = (p - P) / (1 - P)
- Edge detection
- Fractional Kelly scaling
- Min edge / max fraction / min bet guards
- Traditional odds conversion
- Batch and ranking
- The "flipping" insight from the article (same ratio, different sizing)
"""

import pytest
import importlib.util
import os

# Import kelly module directly, bypassing src/__init__.py which needs eth_account
_kelly_path = os.path.join(os.path.dirname(__file__), "..", "src", "kelly.py")
_spec = importlib.util.spec_from_file_location("kelly", _kelly_path)
_kelly = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_kelly)
KellyCriterion = _kelly.KellyCriterion
KellySizing = _kelly.KellySizing


class TestKellyFormula:
    """Test the core Kelly formula F = (p - P) / (1 - P)."""

    def test_basic_calculation(self):
        kelly = KellyCriterion(bankroll=1000, fraction=1.0, min_edge=0)
        s = kelly.calculate(estimated_prob=0.60, market_price=0.40)
        # F = (0.60 - 0.40) / (1 - 0.40) = 0.20 / 0.60 = 0.3333
        assert abs(s.kelly_fraction - 1 / 3) < 0.001
        assert abs(s.edge - 0.20) < 0.001
        assert abs(s.potential_profit - 0.60) < 0.001

    def test_no_edge(self):
        kelly = KellyCriterion(bankroll=1000, fraction=1.0, min_edge=0)
        s = kelly.calculate(estimated_prob=0.40, market_price=0.40)
        assert s.kelly_fraction == 0.0
        assert not s.has_edge

    def test_negative_edge(self):
        kelly = KellyCriterion(bankroll=1000, fraction=1.0, min_edge=0)
        s = kelly.calculate(estimated_prob=0.30, market_price=0.50)
        assert s.kelly_fraction == 0.0
        assert s.edge < 0

    def test_high_edge_cheap_market(self):
        """When market is cheap (P=0.10) and you estimate 0.50."""
        kelly = KellyCriterion(bankroll=1000, fraction=1.0, min_edge=0, max_fraction=1.0)
        s = kelly.calculate(estimated_prob=0.50, market_price=0.10)
        # F = 0.40 / 0.90 = 0.4444
        assert abs(s.kelly_fraction - 0.4444) < 0.001

    def test_edge_near_certainty(self):
        """High probability, high price."""
        kelly = KellyCriterion(bankroll=1000, fraction=1.0, min_edge=0, max_fraction=1.0)
        s = kelly.calculate(estimated_prob=0.95, market_price=0.80)
        # F = 0.15 / 0.20 = 0.75
        assert abs(s.kelly_fraction - 0.75) < 0.001


class TestFlippingInsight:
    """Test the article's insight: same ratio, different Kelly sizing."""

    def test_same_ratio_different_kelly(self):
        """
        Case 1: P=0.2, p=0.4 (2x higher) → F = 0.20 / 0.80 = 0.25
        Case 2: P=0.4, p=0.8 (2x higher) → F = 0.40 / 0.60 = 0.6667

        Both are 2x the market, but Kelly is much more aggressive
        in Case 2 because you win more often AND the payout is solid.
        """
        kelly = KellyCriterion(bankroll=1000, fraction=1.0, min_edge=0, max_fraction=1.0)

        s1 = kelly.calculate(estimated_prob=0.4, market_price=0.2)
        s2 = kelly.calculate(estimated_prob=0.8, market_price=0.4)

        assert s1.kelly_fraction < s2.kelly_fraction
        assert abs(s1.kelly_fraction - 0.25) < 0.001
        assert abs(s2.kelly_fraction - 0.6667) < 0.001


class TestFractionalKelly:
    """Test fractional Kelly scaling."""

    def test_quarter_kelly(self):
        kelly = KellyCriterion(bankroll=1000, fraction=0.25, min_edge=0)
        s = kelly.calculate(estimated_prob=0.60, market_price=0.40)
        full_f = 1 / 3
        assert abs(s.adjusted_fraction - full_f * 0.25) < 0.001

    def test_half_kelly(self):
        kelly = KellyCriterion(bankroll=1000, fraction=0.50, min_edge=0)
        s = kelly.calculate(estimated_prob=0.60, market_price=0.40)
        full_f = 1 / 3
        assert abs(s.adjusted_fraction - full_f * 0.50) < 0.001

    def test_bet_size_scales_with_bankroll(self):
        k1 = KellyCriterion(bankroll=1000, fraction=0.25, min_edge=0)
        k2 = KellyCriterion(bankroll=2000, fraction=0.25, min_edge=0)
        s1 = k1.calculate(0.60, 0.40)
        s2 = k2.calculate(0.60, 0.40)
        assert abs(s2.bet_size - s1.bet_size * 2) < 0.01

    def test_fraction_presets(self):
        assert KellyCriterion.AGGRESSIVE == 0.50
        assert KellyCriterion.STANDARD == 0.25
        assert KellyCriterion.CONSERVATIVE == 0.125
        assert KellyCriterion.CAUTIOUS == 0.0625


class TestGuards:
    """Test minimum edge, maximum fraction, minimum bet guards."""

    def test_min_edge_filter(self):
        kelly = KellyCriterion(bankroll=1000, fraction=0.25, min_edge=0.05)
        # Edge = 0.02 < 0.05 → should not bet
        s = kelly.calculate(estimated_prob=0.42, market_price=0.40)
        assert s.bet_size == 0.0

    def test_min_edge_pass(self):
        kelly = KellyCriterion(bankroll=1000, fraction=0.25, min_edge=0.05)
        s = kelly.calculate(estimated_prob=0.50, market_price=0.40)
        assert s.bet_size > 0

    def test_max_fraction_cap(self):
        kelly = KellyCriterion(bankroll=1000, fraction=1.0, min_edge=0, max_fraction=0.10)
        s = kelly.calculate(estimated_prob=0.90, market_price=0.10)
        # Full Kelly would be huge, but capped at 10%
        assert s.adjusted_fraction <= 0.10
        assert s.bet_size <= 100.0

    def test_min_bet_filter(self):
        kelly = KellyCriterion(bankroll=100, fraction=0.25, min_edge=0, min_bet=5.0)
        # Very small edge → tiny bet → below min_bet
        s = kelly.calculate(estimated_prob=0.42, market_price=0.40)
        assert s.bet_size == 0.0


class TestOddsConversion:
    """Test traditional odds → market price conversion."""

    def test_4x_odds(self):
        """4x odds = 25% implied probability (P = 0.25)."""
        kelly = KellyCriterion(bankroll=1000, fraction=1.0, min_edge=0, max_fraction=1.0)
        s = kelly.calculate_for_odds(estimated_prob=0.40, odds=4.0)
        assert abs(s.market_price - 0.25) < 0.001
        # F = (0.40 - 0.25) / (1 - 0.25) = 0.15 / 0.75 = 0.20
        assert abs(s.kelly_fraction - 0.20) < 0.001

    def test_2x_odds(self):
        """2x odds = 50% implied probability."""
        kelly = KellyCriterion(bankroll=1000, fraction=1.0, min_edge=0, max_fraction=1.0)
        s = kelly.calculate_for_odds(estimated_prob=0.60, odds=2.0)
        assert abs(s.market_price - 0.50) < 0.001

    def test_invalid_odds(self):
        kelly = KellyCriterion()
        with pytest.raises(ValueError):
            kelly.calculate_for_odds(0.50, odds=0)


class TestProperties:
    """Test computed properties on KellySizing."""

    def test_expected_value(self):
        kelly = KellyCriterion(bankroll=1000, fraction=1.0, min_edge=0)
        s = kelly.calculate(estimated_prob=0.60, market_price=0.40)
        # EV = odds × p - 1 = 2.5 × 0.60 - 1 = 0.50
        assert abs(s.expected_value - 0.50) < 0.001

    def test_implied_odds(self):
        kelly = KellyCriterion(bankroll=1000, fraction=1.0, min_edge=0)
        s = kelly.calculate(estimated_prob=0.60, market_price=0.25)
        assert abs(s.implied_odds - 4.0) < 0.001

    def test_shares(self):
        kelly = KellyCriterion(bankroll=1000, fraction=0.25, min_edge=0)
        s = kelly.calculate(estimated_prob=0.60, market_price=0.40)
        assert abs(s.shares - s.bet_size / 0.40) < 0.01


class TestBatchAndRanking:
    """Test batch calculations and opportunity ranking."""

    def test_batch_calculate(self):
        kelly = KellyCriterion(bankroll=1000, fraction=0.25, min_edge=0)
        opps = [(0.60, 0.40), (0.70, 0.30), (0.50, 0.50)]
        results = kelly.batch_calculate(opps)
        assert len(results) == 3
        assert results[0].has_edge
        assert results[1].has_edge
        assert not results[2].has_edge

    def test_rank_opportunities(self):
        kelly = KellyCriterion(bankroll=1000, fraction=0.25, min_edge=0)
        opps = [
            ("A", 0.55, 0.45),
            ("B", 0.70, 0.30),
            ("C", 0.50, 0.50),
        ]
        ranked = kelly.rank_opportunities(opps)
        # B should be first (biggest edge)
        assert ranked[0][0] == "B"
        # C should be last (no edge)
        assert ranked[-1][0] == "C"


class TestBankrollUpdate:
    """Test bankroll management."""

    def test_update_bankroll(self):
        kelly = KellyCriterion(bankroll=1000)
        kelly.update_bankroll(1100)
        assert kelly.bankroll == 1100

    def test_negative_bankroll_rejected(self):
        kelly = KellyCriterion(bankroll=1000)
        with pytest.raises(ValueError):
            kelly.update_bankroll(-100)

    def test_override_bankroll_in_calculate(self):
        kelly = KellyCriterion(bankroll=1000, fraction=0.25, min_edge=0)
        s = kelly.calculate(0.60, 0.40, bankroll=500)
        assert s.bankroll == 500
        # Bet should be half of what it would be with 1000
        s2 = kelly.calculate(0.60, 0.40)
        assert abs(s.bet_size - s2.bet_size / 2) < 0.01


class TestEdgeCases:
    """Test boundary conditions."""

    def test_price_near_zero(self):
        kelly = KellyCriterion(bankroll=1000, fraction=0.25, min_edge=0)
        s = kelly.calculate(estimated_prob=0.50, market_price=0.01)
        assert s.bet_size > 0

    def test_price_near_one(self):
        kelly = KellyCriterion(bankroll=1000, fraction=0.25, min_edge=0)
        s = kelly.calculate(estimated_prob=0.999, market_price=0.99)
        assert s.kelly_fraction >= 0

    def test_prob_clamping(self):
        kelly = KellyCriterion(bankroll=1000, fraction=1.0, min_edge=0)
        s = kelly.calculate(estimated_prob=1.5, market_price=0.40)
        assert s.estimated_prob == 1.0

    def test_zero_bankroll(self):
        kelly = KellyCriterion(bankroll=0, fraction=0.25, min_edge=0)
        s = kelly.calculate(0.60, 0.40)
        assert s.bet_size == 0.0

    def test_summary_with_edge(self):
        kelly = KellyCriterion(bankroll=1000, fraction=0.25, min_edge=0)
        s = kelly.calculate(0.60, 0.40)
        assert "Edge" in s.summary()

    def test_summary_no_edge(self):
        kelly = KellyCriterion(bankroll=1000, fraction=0.25, min_edge=0)
        s = kelly.calculate(0.30, 0.40)
        assert "No edge" in s.summary()

    def test_repr(self):
        kelly = KellyCriterion(bankroll=1000, fraction=0.25)
        assert "1000" in repr(kelly)
        assert "25" in repr(kelly)
