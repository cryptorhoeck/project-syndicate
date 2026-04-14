## PROMPT FOR GEMINI / CHATGPT — Library Textbook Content
## Copy everything below the line and paste it into the chatbot
## ================================================================

I'm building an autonomous AI trading agent system called Project Syndicate. AI agents (powered by Claude API) are spawned with capital and a mandate to make money trading cryptocurrency. They discover their own strategies through experience — no human strategy injection.

The system has a "Library" — educational materials that new agents read during their first orientation cycle. These are textbooks, not instructions. They teach concepts and frameworks, not specific strategies or parameters. Think university education, not a trading manual.

I need you to write 8 condensed textbooks. Each one will be injected into an agent's context window during its very first thinking cycle, so they must be:

- **Under 800 words each** (these get compressed further before injection — token budget is tight)
- **Conceptual, not prescriptive** — teach "what is momentum trading" not "use RSI above 70 to sell"
- **Honest about limitations** — acknowledge when techniques are debated or unreliable
- **Written for an AI agent, not a human** — the reader is a Claude-powered agent that will be making autonomous trading decisions. No hand-holding, no dumbing down. Assume high intelligence but zero trading experience.
- **Formatted as markdown** with clear headers

Here are the 3 textbooks I need:

---

### TEXTBOOK 1: "Thinking Efficiently" (08_thinking_efficiently.md)

This is the MOST IMPORTANT textbook. Every agent reads this first regardless of role. It's about the economics of thinking in a system where API costs directly reduce your P&L.

**Must cover:**
- The Thinking Tax: every API call costs real money, deducted from your profit. Your True P&L = Revenue - Losses - API Costs. Verbose, unproductive thinking literally kills you.
- Analysis paralysis vs decisive action: the cost of overthinking vs the cost of acting on insufficient information. Finding the sweet spot.
- When to research vs when to act: frameworks for deciding "do I need more information or should I commit?"
- The value of going idle: doing nothing is a valid, cost-efficient decision. Not every cycle needs to produce an action. Strategic patience is a skill.
- Learning from others: The Agora (the system's communication channel) contains intel from other agents. Reading is cheaper than re-discovering.
- Decision quality over decision volume: agents are evaluated on outcomes, not activity. 10 good decisions beat 100 mediocre ones.
- Self-notes matter: the notes you write to your future self become your memory and personality. Write sharp, honest self-notes. "Everything is fine" is a useless self-note. "I keep losing on low-volume pairs during weekends — avoid" is valuable.

**Tone:** Direct, slightly urgent. This is survival advice.

---

### TEXTBOOK 2: "Market Mechanics" (01_market_mechanics.md)

Foundational knowledge of how financial markets work at the mechanical level. The plumbing.

**Must cover:**
- Order books: what they are, how they represent supply and demand
- Bid/ask spread: what it means, why it matters, what a tight vs wide spread tells you
- Order types: market orders (instant, pay the spread), limit orders (price control, might not fill), stop-loss orders (protection)
- Maker vs taker fees: how exchanges charge differently based on whether you add or remove liquidity
- Slippage: why your execution price might differ from the price you saw, especially in low-liquidity markets
- Volume: what it means, why volume confirms (or contradicts) price moves
- Trading pairs: base currency vs quote currency, what BTC/USDT means mechanically
- Liquidity: why it matters, how to gauge it, the danger of illiquid markets
- Price discovery: how market price emerges from the interaction of all participants

**Tone:** Clinical, precise. This is engineering documentation for how the machine works.

---

### TEXTBOOK 3: "Risk Management" (03_risk_management.md)

How to think about risk, protect capital, and survive long enough to profit.

**Must cover:**
- Position sizing: never risk more than X% of capital on a single trade. Why this matters mathematically (a 50% loss requires a 100% gain to recover).
- Stop losses: defining your maximum acceptable loss BEFORE entering a trade, not after
- Risk/reward ratio: why you should only take trades where the potential reward justifies the risk (e.g., 2:1 minimum)
- Drawdown management: the psychological and mathematical reality of losing streaks. Even good strategies have bad periods.
- Correlation risk: if you hold 3 positions that all move together, you effectively have 1 big position. Diversification means uncorrelated bets.
- The Warden: this system has an immutable risk layer (The Warden) that enforces hard limits. You cannot override it. Working within Warden limits is not a constraint — it's a survival advantage. Agents that try to push past limits get flagged and die faster.
- Survival > profit: in the early days, not losing money is more important than making money. Dead agents make zero profit. Preservation of capital gives you time to learn.
- The Kelly Criterion concept: there's a mathematically optimal bet size based on your edge and odds. Betting more than Kelly is reckless. Betting less is conservative but safe. Most traders should bet fractional Kelly.

**Tone:** Serious, grounded. This is the difference between surviving and dying.

---

### TEXTBOOK 4: "Strategy Categories" (02_strategy_categories.md)

An overview of major trading strategy families. What they are, how they work conceptually, and their general tradeoffs. This is a menu of possibilities, not a recommendation.

**Must cover:**
- Momentum / trend following: the idea that assets in motion tend to stay in motion. Riding trends rather than predicting reversals. Requires patience and discipline. Works well in trending markets, gets destroyed in choppy sideways markets.
- Mean reversion: the idea that prices tend to return to an average. Buy when something is "too cheap," sell when "too expensive." Opposite of momentum. Works in range-bound markets, fails spectacularly during regime changes.
- Arbitrage (spatial): the same asset trading at different prices on different exchanges. Buy cheap on Exchange A, sell expensive on Exchange B. Theoretically risk-free, practically limited by fees, speed, and transfer times.
- Arbitrage (triangular): exploiting price discrepancies between three related pairs (e.g., BTC/USDT → ETH/BTC → ETH/USDT). Requires fast execution and precise math.
- Market making: providing liquidity by placing both buy and sell orders. Profit from the spread. Requires managing inventory risk — you end up holding assets you didn't necessarily want.
- Breakout strategies: entering when price moves beyond a defined range (support/resistance). Betting that the breakout leads to a sustained move. High false-positive rate.
- Scalping: many small, fast trades capturing tiny price movements. High win rate, small gains per trade. Extremely sensitive to fees and execution speed.
- Swing trading: holding positions for days to weeks, capturing medium-term moves. Less noise than scalping, requires patience and position management.
- Yield strategies: earning returns by lending, staking, or providing liquidity in DeFi. Not trading per se — more like earning interest. Lower risk, lower returns, but smart contract risk is real.
- The honest truth: most strategies work sometimes and fail sometimes. Market regime determines which strategies thrive. There is no permanent edge — only temporary ones that must be continuously discovered and adapted.

**Tone:** Academic but practical. A survey course, not advocacy for any approach.

---

### TEXTBOOK 5: "Crypto Fundamentals" (04_crypto_fundamentals.md)

Foundational knowledge of how cryptocurrency markets and technology work. Essential context for any agent operating in this space.

**Must cover:**
- Blockchain basics: a distributed ledger. Transactions are grouped into blocks, blocks are chained together cryptographically. Immutable once confirmed. Different chains have different properties (speed, cost, security).
- Consensus mechanisms: Proof of Work (Bitcoin — energy-intensive, highly secure) vs Proof of Stake (Ethereum, Solana — energy-efficient, different security tradeoffs). This affects transaction speed and fees.
- Wallets and keys: public keys (your address, safe to share) vs private keys (your access, never share). Custodial wallets (exchange holds your keys) vs non-custodial (you hold your keys). "Not your keys, not your crypto."
- Gas fees: the cost of doing anything on-chain. Varies by network congestion. Ethereum gas can spike 10-100x during high demand. Solana and other chains are much cheaper. Gas fees eat into profitability — always factor them in.
- CEX vs DEX: Centralized exchanges (Binance, Kraken) are fast, liquid, and regulated. Decentralized exchanges (Uniswap, Raydium) are permissionless but have lower liquidity, higher slippage, and smart contract risk.
- Stablecoins: tokens pegged to fiat currencies (USDT, USDC). Essential for trading pairs and parking capital. Not all stablecoins are equally safe — understand the backing mechanism.
- Market cap vs volume: market cap = price × supply (tells you the asset's size). Volume = how much is trading (tells you liquidity and interest). High market cap + low volume = illiquid and dangerous.
- BTC dominance: Bitcoin's share of total crypto market cap. Rising dominance = money flowing to safety. Falling dominance = "alt season" where smaller assets outperform.
- What drives crypto prices: macro sentiment, Bitcoin halving cycles (~4 year cycles), regulatory news, adoption metrics, whale movements, narrative and hype. Crypto is driven by both fundamentals and speculation — often more speculation.
- Regulatory landscape: crypto regulation varies by country and changes frequently. Major regulatory announcements can move markets 10-20% in hours. Stay aware of regulatory risk.

**Tone:** Informative, grounded. No hype, no ideology. Just how the machine works.

---

### TEXTBOOK 6: "Technical Analysis" (05_technical_analysis.md)

Tools for reading price charts and identifying patterns. Presented as analytical tools with acknowledged limitations, not as gospel.

**Must cover:**
- What technical analysis IS: the study of price and volume data to identify patterns that may predict future movements. Based on the idea that market behavior repeats because human psychology is consistent.
- Candlestick charts: each candle shows open, high, low, close for a time period. Green/white = price went up. Red/black = price went down. The body shows open-to-close range, the wicks show the extremes.
- Moving averages: SMA (simple — equal weight to all periods) and EMA (exponential — weights recent data more). Used to smooth noise and identify trend direction. Common periods: 20 (short-term), 50 (medium), 200 (long-term). "Golden cross" (short MA crosses above long MA) is bullish. "Death cross" is the opposite.
- RSI (Relative Strength Index): measures momentum on a 0-100 scale. Above 70 is considered "overbought" (might pull back). Below 30 is "oversold" (might bounce). NOT a timing signal — assets can stay overbought/oversold for extended periods.
- MACD: shows the relationship between two moving averages. Signal line crossovers suggest momentum shifts. Useful for confirming trends, not for predicting reversals.
- Bollinger Bands: a moving average with bands at ±2 standard deviations. Price touching the upper band suggests high relative to recent history; lower band suggests low. Band width indicates volatility.
- Volume analysis: volume confirms price moves. Price up + volume up = strong move. Price up + volume down = weak move, likely to reverse. Always check volume.
- Support and resistance: price levels where buying (support) or selling (resistance) has historically concentrated. Not magic lines — just areas where supply/demand shifted before. They break eventually.
- Timeframe selection: the same chart looks different on 1-minute, 1-hour, and 1-day timeframes. Shorter timeframes have more noise. Longer timeframes show clearer trends but slower signals. Your trading strategy determines your timeframe.
- The limitations: technical analysis is probabilistic, not deterministic. Patterns fail. Indicators lag. In crypto especially, fundamental news (hacks, regulations, listings) can override any technical setup instantly. TA is one tool in the toolkit, not the whole toolkit.

**Tone:** Balanced and honest. Teach the tools, but don't oversell them.

---

### TEXTBOOK 7: "DeFi Protocols" (06_defi_protocols.md)

How decentralized finance protocols work mechanically. The building blocks of on-chain yield generation.

**Must cover:**
- What DeFi IS: financial services (lending, borrowing, trading, insurance) built on smart contracts instead of banks. Permissionless — anyone (or any agent) can interact. No KYC, no middlemen, but also no safety net.
- Lending and borrowing: protocols like Aave and Compound let you deposit assets to earn interest, or borrow against collateral. Interest rates are algorithmic — they rise with demand. Over-collateralization is required (deposit $150 to borrow $100). If your collateral value drops too far, you get liquidated automatically.
- Liquidity pools: instead of order books, DEXes use pools of paired assets (e.g., ETH + USDT). Traders swap against the pool. Liquidity providers deposit both assets and earn trading fees.
- Automated Market Makers (AMMs): the math behind liquidity pools. Most use the constant product formula (x × y = k). As one asset is bought from the pool, its price automatically increases. Simple, elegant, but creates impermanent loss.
- Impermanent loss: when you provide liquidity and the price ratio of the paired assets changes, you end up with less value than if you had just held. Called "impermanent" because it reverses if prices return to the original ratio. In practice, it's often permanent. This is the hidden cost of being a liquidity provider.
- Yield farming: chasing the highest returns by moving capital between protocols. Protocols offer token incentives to attract liquidity. Returns can be high (100%+ APY) but are usually temporary — they drop as more capital floods in. High yield almost always means high risk.
- Staking: locking tokens to help secure a Proof of Stake network. Earns a steady return (typically 3-8% annually). Lower risk than farming, but your tokens are locked for a period and you're exposed to price risk on the staked asset.
- Smart contract risk: the code IS the system. If there's a bug, funds can be drained. Even audited contracts have been exploited. This is the fundamental risk of DeFi — there's no customer support to call if something goes wrong.
- Oracle dependencies: DeFi protocols need external price feeds (oracles like Chainlink) to know asset values. Oracle manipulation can be used to exploit protocols. It's an attack vector that's been used repeatedly.
- Bridges: moving assets between blockchains. Bridges are historically the weakest link in DeFi — they hold enormous value and have been the target of the largest hacks in crypto history. Use with extreme caution.

**Tone:** Technical but clear. Emphasize both the opportunity and the genuine danger.

---

### TEXTBOOK 8: "Exchange APIs" (07_exchange_apis.md)

How to interact with cryptocurrency exchanges programmatically. The technical interface between agents and markets.

**Must cover:**
- What ccxt IS: a unified library that provides a single interface to 100+ cryptocurrency exchanges. Instead of learning each exchange's unique API, you learn ccxt and it handles the translation. This is the primary tool agents use to interact with markets.
- REST vs WebSocket: REST APIs are request-response (you ask, they answer). Good for placing orders, checking balances, fetching historical data. WebSocket APIs are persistent streams — you connect once and receive real-time updates. Good for live price feeds and order book updates. REST for actions, WebSocket for monitoring.
- Rate limits: exchanges limit how many API calls you can make per minute. Exceeding rate limits gets you temporarily banned (usually 1-5 minutes). Different endpoints have different limits. Always respect rate limits — getting banned mid-trade is catastrophic. Implement exponential backoff on retries.
- Order lifecycle: an order goes through stages: created → open (on the order book) → partially filled → filled (complete) → OR cancelled. Limit orders may never fill if the price doesn't reach your level. Always check order status after placement.
- Order types in practice: market orders execute immediately at the best available price (fast but you pay the spread). Limit orders execute only at your specified price or better (price control but might not fill). Stop-loss orders trigger a market sell when price drops to your level (protection, but in flash crashes the execution price can be far below your stop).
- Reading market data: tickers (current price snapshot), OHLCV (historical candles — open/high/low/close/volume), order book (current bids and asks showing depth). Each serves a different analytical purpose.
- Authentication: API keys come in pairs — a key (identifies you) and a secret (proves it's you). Some exchanges also require a passphrase. NEVER share your secret. Use read-only keys when you only need data. Use trade-enabled keys only when execution is required. In this system, the Warden controls all trade execution — agents request, the Warden approves and routes.
- Sandbox/testnet: most major exchanges offer test environments with fake money. Use these for testing strategies without risking real capital. Behavior is similar but not identical to production — liquidity is lower and some edge cases differ.
- Error handling: exchange APIs fail regularly. Timeouts, rate limits, maintenance windows, network issues. Every API call must have error handling with retries. Assume any call can fail. Log everything. Never assume an order placed is an order filled — always verify.
- Fees matter: maker fees, taker fees, withdrawal fees, network fees. On small positions, fees can eat your entire profit. Always calculate fees before entering a trade. A 0.1% edge means nothing if you're paying 0.2% in fees round-trip.

**Tone:** Practical, engineering-focused. This is a user manual for the tools of the trade.

---

**Output format:** Give me each textbook as a separate markdown document with the filename as a header. I'll copy each one into the project.
