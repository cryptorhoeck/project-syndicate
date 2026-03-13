# Market Mechanics

## Order Books

An order book is the central data structure of any exchange. It is a real-time ledger of all outstanding buy orders (bids) and sell orders (asks) for a given trading pair.

Bids are sorted highest-first. Asks are sorted lowest-first. The gap between the highest bid and lowest ask is the spread. The order book represents the current state of supply and demand -- it shows you exactly how much capital is willing to buy or sell at each price level.

## Bid/Ask Spread

The spread is the difference between the highest bid and lowest ask -- it is the cost of immediacy. If you buy and immediately sell, you lose the spread.

- **Tight spread** (small gap): high liquidity, active market, lower cost to trade.
- **Wide spread** (large gap): low liquidity, fewer participants, higher cost to trade.

The spread is a transaction cost that exists on every round-trip trade. It must be factored into every profitability calculation.

## Order Types

**Market orders** execute immediately at the best available price. You get speed and certainty of execution, but you pay the spread and accept whatever price the market gives you.

**Limit orders** specify the maximum price you'll pay (buy) or minimum you'll accept (sell). They give you price control but may never fill if the market doesn't reach your level. Unfilled limit orders sit on the order book until cancelled.

**Stop-loss orders** trigger a market sell when the price drops to a specified level. They are protective -- designed to cap your downside. In fast-moving markets, the actual execution price may be significantly worse than the stop level (slippage through the stop).

## Maker vs. Taker Fees

Exchanges charge differently based on your role:

- **Makers** add liquidity by placing limit orders that rest on the book. They typically pay lower fees (sometimes zero or even negative -- the exchange pays you).
- **Takers** remove liquidity by placing market orders or limit orders that fill immediately. They pay higher fees.

This fee structure incentivizes patient, limit-order-based trading. Over many trades, the difference between maker and taker fees compounds significantly.

## Slippage

Slippage is the difference between the price you expected and the price you got. It occurs because:

- The order book changed between your price check and your order execution
- Your order is large enough to consume multiple price levels in the book
- Market conditions shifted during processing

Slippage is worse in illiquid markets and during volatile periods. It is an invisible cost that can turn a profitable trade into a losing one.

## Volume

Volume measures how much of an asset has been traded over a given period. It indicates:

- **Market interest:** High volume means many participants are active.
- **Move conviction:** A price increase on high volume is more meaningful than one on low volume.
- **Liquidity proxy:** Higher volume generally means tighter spreads and less slippage.

Volume divergence -- price moving one direction while volume moves the other -- is a classic warning signal that a move may not be sustainable.

## Trading Pairs

A trading pair defines what you're exchanging. In BTC/USDT:

- **BTC** is the base currency (what you're buying or selling)
- **USDT** is the quote currency (what you're pricing it in)

A price of 60,000 means 1 BTC costs 60,000 USDT. When you "buy BTC/USDT," you spend USDT to receive BTC. When you "sell," you spend BTC to receive USDT.

Understanding which asset you hold at any given time is critical for position management and risk calculation.

## Liquidity

Liquidity is the ability to buy or sell an asset quickly without significantly moving the price. It is determined by order book depth, trading volume, and the number of active participants.

Illiquid markets are dangerous:
- Spreads are wider (higher cost)
- Slippage is greater (worse execution)
- Exiting a position quickly may be impossible at a reasonable price
- Prices can gap -- jump from one level to another with no trades in between

Always check liquidity before entering a position. Getting into a trade is easy. Getting out at the price you want is the hard part.

## Price Discovery

The "market price" is not a fixed number. It is the continuously shifting consensus of all participants, expressed through the order book. Every trade that executes updates the last traded price. Every new order placed shifts the balance of supply and demand.

Price emerges from the interaction of informed traders, uninformed traders, market makers, arbitrageurs, and algorithms -- all acting on different information, timeframes, and objectives. No single participant sets the price. The market is the sum of all participants.
