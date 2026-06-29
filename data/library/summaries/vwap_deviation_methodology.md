# VWAP Deviation Methodology -- Summary

**VWAP** (volume-weighted average price) approximates the fair value most volume actually traded at: cumulative (typical price x volume) divided by cumulative volume, where typical price = (high + low + close) / 3. More meaningful than a simple moving average because it weights by volume. Anchored and rolling VWAP read differently -- be explicit which you use.

**Deviation bands** (VWAP plus or minus k standard deviations) turn VWAP into dynamic support/resistance and a fair-value gauge. Price oscillates around VWAP; how far it deviates is the signal. Small deviations are noise.

**Two strategies, one indicator:** mean reversion (buy far below VWAP, sell far above) works in ranging markets; trend following (buy the cross above, sell the cross below) works in trends. Using the wrong mode for the regime is the classic failure -- mean-reverting in a strong trend means fighting the move.

**Regime adaptation** is essential: trend-follow in bull/bear, mean-revert in chop, and bias with the dominant force (suppress shorts in a bull regime).

**Confirmations** strengthen a deviation signal: RSI oversold/overbought (agreement, not a trigger -- trends stay extended), a genuine volume spike (above ~2x average), and momentum. Convergence raises confidence; none alone is decisive.

**Caveats:** mean reversion has unbounded downside in a regime change ("cheap" gets cheaper) -- always stop, never average down blindly. VWAP is lagging and mean-based. Every signal is a hypothesis, sized and risk-managed, not a directive.
