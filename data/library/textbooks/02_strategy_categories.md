# Strategy Categories

This is a survey of major trading strategy families. Each has conditions where it thrives and conditions where it fails. No strategy works all the time. Your job is to understand the landscape, not to pick a favorite.

## Momentum / Trend Following

Assets in motion tend to stay in motion. When a price is rising on increasing volume, momentum traders ride the trend rather than predict where it ends.

**Strengths:** Can capture large moves. Requires no prediction of exact tops or bottoms.
**Weaknesses:** Gets destroyed in sideways, choppy markets. Whipsaws -- false signals where trends reverse immediately -- erode capital. Requires discipline to hold through pullbacks and to exit when the trend breaks.

## Mean Reversion

Prices tend to return to an average over time. When an asset deviates significantly from its historical norm, mean reversion traders bet on a return to that norm.

**Strengths:** High win rate in range-bound markets. Clear entry signals based on deviation.
**Weaknesses:** Fails catastrophically during regime changes. What looks "too cheap" can go to zero. What looks "too expensive" can keep going up for months. The average itself shifts over time.

## Spatial Arbitrage

The same asset trades at different prices on different exchanges. Buy where it's cheap, sell where it's expensive, pocket the difference.

**Strengths:** Theoretically risk-free. Profit is defined at entry.
**Weaknesses:** Requires fast execution. Transfer times between exchanges create exposure. Fees, withdrawal limits, and network congestion can eliminate the spread. Competition from other arbitrageurs compresses opportunities to near-zero.

## Triangular Arbitrage

Exploit price discrepancies between three related pairs. For example: BTC/USDT -> ETH/BTC -> ETH/USDT. If the implied cross-rate differs from the actual rate, there's a profit opportunity.

**Strengths:** Can be executed on a single exchange (no transfer risk).
**Weaknesses:** Requires very precise math and fast execution. Opportunities are tiny and fleeting. Fees can easily exceed the arbitrage profit. Intense competition from specialized bots.

## Market Making

Place both buy and sell orders around the current price, profiting from the spread between them. You provide liquidity to the market and earn a small amount on each round trip.

**Strengths:** Consistent small profits in stable markets. Maker fee rebates improve margins.
**Weaknesses:** Inventory risk -- you accumulate assets you may not want as the market moves against you. In a sharp trend, one side of your orders gets filled repeatedly while the other doesn't, leaving you with a large directional position.

## Breakout Strategies

Enter a position when price moves beyond a defined boundary -- above resistance or below support. The bet is that breaking through a significant level leads to a sustained move in that direction.

**Strengths:** Can capture the beginning of large trends.
**Weaknesses:** High false-positive rate. Many apparent breakouts reverse immediately (fakeouts). Requires precise identification of meaningful levels, which is more art than science.

## Scalping

Make many small, fast trades capturing tiny price movements. Hold positions for seconds to minutes. Aim for a high win rate with small gains per trade.

**Strengths:** Low per-trade risk. Profits compound with volume.
**Weaknesses:** Extremely sensitive to fees -- even small fee increases can turn the strategy negative. Requires excellent execution speed. Slippage on any individual trade can wipe out many winning trades. Mentally exhausting for human traders; computationally demanding for agents.

## Swing Trading

Hold positions for days to weeks, capturing medium-term price moves. Uses a combination of technical and fundamental analysis to identify entry and exit points.

**Strengths:** Less noise than shorter timeframes. Lower fee burden (fewer trades). More time to analyze and adjust.
**Weaknesses:** Overnight and weekend risk -- markets move while you're not watching. Requires patience and the ability to sit through adverse moves without panicking.

## Yield Strategies

Earn returns by providing liquidity, lending assets, or staking tokens in DeFi protocols. Not active trading -- more analogous to earning interest.

**Strengths:** Generates returns without requiring directional predictions. Can be relatively passive.
**Weaknesses:** Smart contract risk (protocol exploits). Impermanent loss for liquidity providers. Yield rates are variable and often temporary. High advertised APYs usually come with high risk.

## The Honest Truth

Most strategies work sometimes and fail sometimes. The dominant factor is **market regime** -- the prevailing condition of the market at any given time. Trending markets reward momentum. Range-bound markets reward mean reversion. Volatile markets reward options-like strategies. Quiet markets reward yield farming.

There is no permanent edge. Every edge degrades as others discover and exploit it. Success requires continuously discovering which strategies fit current conditions and having the discipline to stop using a strategy when conditions change -- even if it worked yesterday.
