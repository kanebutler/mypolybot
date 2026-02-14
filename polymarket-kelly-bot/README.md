# Polymarket Kelly Bot

A Polymarket trading bot with **Kelly Criterion position sizing**.

Inspired by [discountry/polymarket-trading-bot](https://github.com/discountry/polymarket-trading-bot), rebuilt from scratch with mathematically optimal position sizing.

## The Kelly Formula for Prediction Markets

```
F = (p - P) / (1 - P)

Position Size = Edge / Potential Profit
```

In practice, use **fractional Kelly** (1/4 default) to reduce variance.

## Quick Start

```bash
pip install -r requirements.txt
python examples/quickstart.py   # See Kelly calculations
pytest tests/ -v                # Run tests
```

## Usage

```python
from src.kelly import KellyCriterion

kelly = KellyCriterion(bankroll=1000, fraction=0.25)
sizing = kelly.calculate(estimated_prob=0.60, market_price=0.40)
print(sizing.summary())
# Edge: 20.00% | Bet: $83.33 | Shares: 208.3 @ 0.40
```

## License

MIT
