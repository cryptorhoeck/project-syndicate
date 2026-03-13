# Exchange APIs

## What ccxt Is

ccxt is a unified library providing a single interface to 100+ cryptocurrency exchanges. Learn ccxt's interface and it handles the translation to each exchange's unique endpoints, auth, and data formats. This is the primary tool agents use to interact with markets.

## REST vs WebSocket

**REST APIs:** Request-response. Stateless. Use for placing orders, checking balances, fetching historical data.

**WebSocket APIs:** Persistent connection with real-time streams. Use for live price feeds, order book updates, order status changes.

REST is for actions. WebSocket is for monitoring.

## Rate Limits

Every exchange limits how many API calls you can make per time window. Typical limits range from 10 to 1200 requests per minute, varying by endpoint.

Exceeding limits results in temporary bans (1-5 minutes). Getting banned mid-trade is catastrophic. Implement exponential backoff, space requests, and cache when freshness isn't critical.

## Order Lifecycle

An order progresses through defined states:

1. **Created** -- submitted to the exchange
2. **Open** -- accepted and resting on the order book (limit orders)
3. **Partially filled** -- some but not all quantity has executed
4. **Filled** -- fully executed
5. **Cancelled** -- removed before filling (by you or by the exchange)

Limit orders may never fill. Partially filled orders leave you with less than intended. Always verify order status after placement -- never assume submission equals execution.

## Order Types in Practice

**Market orders:** Immediate execution, price uncertainty. In thin markets, fills across multiple levels.

**Limit orders:** Price certainty, execution uncertainty -- may never fill.

**Stop-loss orders:** Trigger a market sell at a specified level. In flash crashes, execution can be far below your stop price.

## Reading Market Data

**Tickers:** Current snapshot -- last price, bid, ask, 24h volume/high/low.

**OHLCV (candles):** Historical open/high/low/close/volume per period. Foundation for technical analysis.

**Order book:** Current bids and asks with quantity at each level. Reveals depth but changes constantly.

## Authentication

API access requires credentials, typically:

- **API key:** Identifies your account. Semi-public.
- **API secret:** Proves your identity. Must be kept absolutely confidential.
- **Passphrase:** Some exchanges require a third credential.

Use minimum permissions: read-only for data, trade-enabled only when needed. In this system, the Warden controls all trade execution -- agents submit requests, the Warden approves and routes them.

## Sandbox and Testnet

Most major exchanges offer test environments with simulated funds. Use these for strategy testing without risking real capital.

Sandboxes approximate production but aren't identical -- lower liquidity, fewer pairs, edge cases not well-represented. Test in sandbox first, then with minimal real capital before scaling.

## Error Handling

Exchange APIs fail regularly -- timeouts, rate limits, maintenance, bugs. This is normal operation, not exceptional.

Every call needs error handling with retries. The most dangerous error is silent: an order you think was placed but wasn't, or cancelled but is still active. Always verify order status independently.

## Fees Matter

Every trade involves multiple fee layers:

- **Maker/taker fees:** 0.01% to 0.1% per side, depending on the exchange and your volume tier.
- **Withdrawal fees:** Fixed or variable, per asset, per network.
- **Network (gas) fees:** For on-chain transactions.

On a round-trip trade (buy + sell), you pay fees twice. A position that gains 0.15% is actually a loss if you're paying 0.1% maker/taker fees per side (0.2% total). Always calculate the full fee structure before entering a trade. Your edge must exceed your total costs -- otherwise you're paying to lose money slowly.
