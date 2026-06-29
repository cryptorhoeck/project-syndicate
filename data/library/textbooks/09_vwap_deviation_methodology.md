# VWAP Deviation Methodology

## What VWAP Is

VWAP (Volume-Weighted Average Price) is the average price over a period, weighted by volume. Unlike a simple moving average, it gives more weight to prices where more volume actually traded, so it approximates the fair value most participants transacted at. Benchmark-driven institutions use it to judge execution quality.

It is computed as the cumulative sum of (typical price x volume) divided by the cumulative sum of volume, where typical price = (high + low + close) / 3. It can be anchored (reset each day or week) or rolling over a fixed window. Be explicit about which you use -- they give different readings.

## Deviation Bands

Price rarely sits exactly on VWAP; it oscillates around it. Measuring how far price has deviated -- in percent, or in standard-deviation bands -- turns VWAP into a dynamic support/resistance line and a fair-value gauge. Bands are typically VWAP plus or minus k times the standard deviation of the price-to-VWAP deviation (k is often 2).

The deviation itself is the signal: small deviations are noise; large ones are where the methodology has something to say.

## Two Strategies From One Indicator

The same VWAP supports opposite tactics depending on market state:

- **Mean reversion:** when price deviates far below VWAP it is "cheap" relative to fair value -- a contrarian long; far above, a contrarian short. Works in ranging, choppy markets.
- **Trend following:** a decisive cross of price through VWAP signals a shift -- buy the breakout above, sell the breakdown below. Works in trending markets.

Choosing the wrong mode for the regime is the classic failure. Mean-reverting in a strong trend means fighting the move -- repeatedly buying a market that keeps falling. Trend-following in a chop means getting whipsawed by every cross.

## Regime Adaptation

Because the right mode depends on the regime, a robust VWAP system reads the regime first:

- **Bull / trending up:** trend-following; bias toward longs, suppress shorts.
- **Bear / trending down:** trend-following; bias toward shorts, suppress longs.
- **Ranging / crab:** mean reversion around VWAP.

A simple bias rule -- for example, in a bull regime drop short signals and scale up long confidence -- keeps the strategy from fighting the dominant force.

## Confirmations

A VWAP deviation is stronger when corroborated, not taken alone:

- **RSI:** oversold (below 30) supports a mean-reversion long; overbought (above 70) supports a short. But assets stay overbought or oversold for long stretches in trends -- treat RSI as agreement, not a trigger.
- **Volume:** a deviation on a genuine volume spike (for example, current volume above twice its recent average) is more meaningful than one on thin volume. A spike with an up-move favors a long; with a down-move, a short.
- **Momentum:** rate-of-change confirms whether a move has force behind it or is merely drifting.

No single confirmation is decisive; convergence of several raises confidence.

## Honest Caveats

- VWAP is a lagging, mean-based measure. It tells you where value was, not where it is going.
- Mean reversion has unbounded downside in a regime change -- "cheap" can get much cheaper. Always pair it with a stop; never average down blindly.
- Like all technical analysis, this is probabilistic. Every deviation signal is a hypothesis to be sized and risk-managed, not a directive.

## Why It Can Work

Markets oscillate around participant-weighted fair value because liquidity providers and benchmark-driven institutions transact relative to VWAP. The edge is not prediction; it is a disciplined, regime-aware framework for buying below and selling above a defensible fair-value estimate, with explicit confirmation and strict risk control.
