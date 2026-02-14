"""
Quickstart Example - Polymarket Kelly Bot

Shows how to use the Kelly Criterion for position sizing
on Polymarket prediction markets.

Usage:
    python examples/quickstart.py
"""

import asyncio
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Import kelly module directly (doesn't need eth_account or web3)
import importlib.util
_path = os.path.join(os.path.dirname(__file__), "..", "src", "kelly.py")
_spec = importlib.util.spec_from_file_location("kelly", _path)
_m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_m)
KellyCriterion = _m.KellyCriterion


def kelly_demo():
    """Demonstrate Kelly Criterion calculations."""

    print("=" * 70)
    print("  Polymarket Kelly Bot - Position Sizing Demo")
    print("=" * 70)

    kelly = KellyCriterion(bankroll=1000.0, fraction=0.25)  # 1/4 Kelly

    print(f"\nBankroll: ${kelly.bankroll:.2f}")
    print(f"Fractional Kelly: {kelly.fraction:.0%}")
    print()

    # ── Example 1: Strong edge on a cheap market ──────────────────────
    print("─" * 70)
    print("Example 1: You think 40%, market says 20% (cheap market)")
    sizing = kelly.calculate(estimated_prob=0.40, market_price=0.20)
    print(f"  {sizing.summary()}")
    print()

    # ── Example 2: Same 2x ratio but expensive market ─────────────────
    print("Example 2: You think 80%, market says 40% (same 2x ratio)")
    sizing = kelly.calculate(estimated_prob=0.80, market_price=0.40)
    print(f"  {sizing.summary()}")
    print()

    # ── Example 3: No edge ────────────────────────────────────────────
    print("Example 3: You agree with the market (no edge)")
    sizing = kelly.calculate(estimated_prob=0.50, market_price=0.50)
    print(f"  {sizing.summary()}")
    print()

    # ── Example 4: Small edge ─────────────────────────────────────────
    print("Example 4: Tiny edge (below min_edge threshold)")
    sizing = kelly.calculate(estimated_prob=0.51, market_price=0.50)
    print(f"  {sizing.summary()}")
    print()

    # ── Example 5: Using traditional odds ─────────────────────────────
    print("Example 5: Traditional odds format (4:1 odds, 40% estimate)")
    sizing = kelly.calculate_for_odds(estimated_prob=0.40, odds=4.0)
    print(f"  {sizing.summary()}")
    print()

    # ── Comparing Kelly fractions ─────────────────────────────────────
    print("─" * 70)
    print("Comparing Kelly fractions (p=0.60, P=0.35):")
    print()
    for name, frac in [
        ("Full Kelly    ", 1.0),
        ("1/2 Kelly     ", 0.50),
        ("1/4 Kelly     ", 0.25),
        ("1/8 Kelly     ", 0.125),
    ]:
        k = KellyCriterion(bankroll=1000, fraction=frac)
        s = k.calculate(0.60, 0.35)
        print(f"  {name} (f={frac:.2f}): bet ${s.bet_size:>7.2f}  ({s.shares:>5.1f} shares)")
    print()

    # ── Ranking multiple opportunities ────────────────────────────────
    print("─" * 70)
    print("Ranking multiple opportunities:")
    print()

    opportunities = [
        ("BTC Up",   0.55, 0.45),
        ("ETH Down", 0.70, 0.40),
        ("SOL Up",   0.45, 0.30),
        ("XRP Down", 0.52, 0.50),
    ]

    ranked = kelly.rank_opportunities(opportunities)
    for i, (label, sizing) in enumerate(ranked, 1):
        if sizing.has_edge:
            print(f"  #{i} {label:12s} edge={sizing.edge:+.2%}  bet=${sizing.bet_size:.2f}  shares={sizing.shares:.1f}")
        else:
            print(f"  #{i} {label:12s} NO EDGE")

    print()
    print("=" * 70)
    print("  To trade live, set POLY_PRIVATE_KEY and POLY_SAFE_ADDRESS")
    print("  then run: python examples/live_kelly.py")
    print("=" * 70)


if __name__ == "__main__":
    kelly_demo()
