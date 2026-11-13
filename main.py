"""Dip-buy mean-reversion bot for make.fun btc-race-24h-20261113-2314.

Strategy (long only, one position, whole cash per trade):
  - Track EMA(25) of the 1-minute closes of CBBTC, seeded as the mean of
    its first 25 closes, then EMA += 2/26 * (close - EMA).
  - Buy the full cash when the close is at least 70 bps below the EMA
    (a dip) and we hold nothing.
  - Sell the whole position when the close is at least 65 bps above the
    EMA (the dip has reverted past the mean), or when the position is
    worth 100 bps less than its cost (stop-loss).
  - Never more than one order per reply; max slippage 100 bps per order.

Why this shape: the dev window is a slow grind down (-4.9% over 24h)
with 1-minute volatility of only ~8 bps against a ~26 bps round-trip
cost, so trend-following and frequent trading both lose money after
fees. The only trades that clear costs are deep dips that revert:
buying 70 bps under a 25-minute mean and selling 65 bps over it.
On the frozen dev window this makes +$11.33 on $1,000 (4 trades,
max drawdown 1.42%). The sell threshold sits in the middle of a flat
plateau (sell +50..+70 bps all profitable, +63..+66 bps identical at
+$11.33), not on an isolated spike. The buy threshold and EMA span are
more sensitive (neighbouring values lose money on the dev window), so
only the sell leg was tuned; EMA and buy threshold stay at their
original values.

Deterministic: standard library only, no network, no wall clock, no
files outside the repo, no randomness. Same bars give the same orders.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

SYMBOL = "CBBTC"
EMA_SPAN = 25
BUY_BPS = 70       # buy when close is this far below the EMA (bps)
SELL_BPS = 65       # sell when close is this far above the EMA (bps)
STOP_BPS = 100     # sell when position value is this far below cost (bps)
MAX_SLIPPAGE_BPS = 100


class Ema:
    """One EMA: the mean of its first span of closes, then 2/(span+1) steps."""

    def __init__(self, span):
        self.span = span
        self.warm = []
        self.value = None

    def push(self, close):
        if self.value is not None:
            self.value += (2.0 / (self.span + 1)) * (close - self.value)
            return
        self.warm.append(close)
        if len(self.warm) == self.span:
            self.value = sum(self.warm) / self.span


ema = Ema(EMA_SPAN)


def on_batch(state, batch):
    bar = None
    for b in batch["bars"]:
        if b["symbol"] == SYMBOL:
            bar = b
            break
    if bar is not None:
        ema.push(float(bar["c"]))
    if state.warming_up or ema.value is None or bar is None:
        return []
    close = float(bar["c"])
    # Deviation of the close from the EMA, in basis points.
    z_bps = (close - ema.value) / ema.value * 10000.0
    held = state.positions.get(SYMBOL)
    if held is None:
        if z_bps <= -BUY_BPS:
            notional = state.cash
            if notional >= int(state.hello["fees"]["min_notional_quote_units"]):
                return [{"side": "buy", "symbol": SYMBOL,
                         "notional_quote_units": notional,
                         "max_slippage_bps": MAX_SLIPPAGE_BPS}]
        return []
    # We hold: sell on reversion past the mean, or on the stop-loss.
    if z_bps >= SELL_BPS:
        return [{"side": "sell", "symbol": SYMBOL, "qty": held.qty,
                 "max_slippage_bps": MAX_SLIPPAGE_BPS}]
    value = state.value(SYMBOL, held.qty)
    # value <= cost * (1 - STOP_BPS/10000), in integers.
    if value * 10000 <= held.cost * (10000 - STOP_BPS):
        return [{"side": "sell", "symbol": SYMBOL, "qty": held.qty,
                 "max_slippage_bps": MAX_SLIPPAGE_BPS}]
    return []


makefun_bars.run(on_batch)
