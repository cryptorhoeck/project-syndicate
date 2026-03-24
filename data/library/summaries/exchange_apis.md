# Exchange APIs — Summary

**ccxt** provides a unified interface to 100+ exchanges. Learn ccxt and it handles translation to each exchange's unique API. This is the primary tool agents use to interact with markets.

**REST** is for actions (place orders, check balances, fetch history). **WebSocket** is for monitoring (live price feeds, order book updates, order status). Use the right tool for the job.

**Rate limits** restrict API calls per time window (10-1200 per minute depending on exchange and endpoint). Exceeding limits gets you temporarily banned. Getting banned mid-trade is catastrophic. Always implement backoff and caching.

**Order lifecycle:** Created → Open → Partially filled → Filled / Cancelled. Limit orders may never fill. Always verify status after placement — never assume submission equals execution.

**Market orders** give speed but uncertain price. **Limit orders** give price control but uncertain execution. **Stop-loss orders** cap downside but can slip badly in flash crashes.

**Market data types:** Tickers (current snapshot), OHLCV candles (historical price/volume), Order book (current bids/asks with depth). Each serves a different analytical purpose.

**Security:** API keys identify you, API secrets prove identity. Use minimum permissions — read-only for data, trade-enabled only when needed. In this system, the Warden controls all execution.

**Fees compound:** Maker/taker fees (0.01-0.1% per side), withdrawal fees, and gas fees. A round-trip trade paying 0.1% each side costs 0.2% total. Your edge must exceed total costs or you're paying to lose slowly.
