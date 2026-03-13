# Crypto Fundamentals

## Blockchain Basics

A blockchain is a distributed ledger -- a database replicated across thousands of computers, where transactions are grouped into blocks and chained together using cryptographic hashes. Once a block is confirmed, it is effectively immutable. Tampering with one block would invalidate every subsequent block.

Different blockchains optimize for different properties. Bitcoin prioritizes security and decentralization at the cost of speed. Solana prioritizes speed. Ethereum attempts a middle path. These tradeoffs affect transaction costs, confirmation times, and supported applications.

## Consensus Mechanisms

**Proof of Work (PoW):** Miners compete to solve computational puzzles. Energy-intensive but battle-tested -- Bitcoin has run on PoW since 2009 without a successful core protocol attack.

**Proof of Stake (PoS):** Validators lock tokens as collateral, selected proportional to their stake. Energy-efficient. Dishonest validators get slashed. Ethereum, Solana, and most newer chains use PoS. Different security model -- risk shifts from energy cost to capital cost.

## Wallets and Keys

**Public key:** Your address. Safe to share. Others use it to send you funds.
**Private key:** Your proof of ownership. Never share it. Whoever has the private key controls the funds.

**Custodial wallets** (exchanges): the exchange holds your keys. Convenient, but exchanges have been hacked, frozen accounts, and gone bankrupt. **Non-custodial wallets:** you hold your keys. Full control, full responsibility. "Not your keys, not your crypto."

## Gas Fees

Every on-chain operation costs gas -- a fee paid to validators for processing your transaction. Gas prices fluctuate with network demand.

On Ethereum, gas can spike 10-100x during congestion. Solana and other chains are much cheaper but have their own patterns. Gas fees directly reduce profitability -- always factor them into any on-chain strategy.

## CEX vs DEX

**Centralized exchanges (CEX):** Binance, Kraken, Coinbase. Fast execution, deep liquidity. You trust the exchange with your funds. Subject to downtime and regulatory actions.

**Decentralized exchanges (DEX):** Uniswap, Raydium, Jupiter. Permissionless, non-custodial. But: lower liquidity, higher slippage, smart contract risk, gas costs. No customer support.

## Stablecoins

Stablecoins are tokens designed to maintain a 1:1 peg with a fiat currency, usually USD. They are essential infrastructure -- the base currency for most trading pairs and the default way to "park" capital without exiting crypto.

Not all stablecoins are equal:
- **USDT (Tether):** Largest by market cap. Questions persist about the quality of its reserves.
- **USDC (Circle):** Fully regulated, transparent reserves. Froze addresses during the Tornado Cash sanctions.
- **DAI/FRAX:** Algorithmic or crypto-collateralized. Different risk profile -- depends on smart contracts and collateral health.

Stablecoin depegs happen. When they do, the effects cascade through every trading pair that uses them as a quote currency.

## Market Cap vs Volume

**Market cap** = price x circulating supply. Tells you the asset's total valuation. A $10B market cap asset is generally more stable and liquid than a $10M one.

**Volume** = total amount traded over a period. Tells you how active the market is. High volume means easier entry and exit. Low volume means potential liquidity traps.

**Dangerous combination:** High market cap but low volume. The price may look stable, but you won't be able to exit a large position without significant slippage.

## BTC Dominance

Bitcoin dominance is Bitcoin's share of the total crypto market capitalization. It is a macro sentiment indicator.

- **Rising dominance:** Capital flowing into Bitcoin, away from altcoins. Risk-off behavior. Flight to relative safety.
- **Falling dominance:** Capital flowing into altcoins. Risk-on behavior. Often called "alt season."

BTC dominance does not tell you direction -- both BTC and alts can fall while dominance rises (if alts fall faster). It tells you where capital is flowing within the crypto ecosystem.

## What Drives Crypto Prices

- **Macro sentiment:** Interest rates, inflation data, and risk appetite affect all speculative assets.
- **Bitcoin halving cycles:** Roughly every 4 years, Bitcoin's mining reward halves. Historically associated with major bull runs, though past performance guarantees nothing.
- **Regulatory news:** A single regulatory announcement can move markets 10-20% in hours. Ban threats, ETF approvals, enforcement actions.
- **Adoption metrics:** Wallet growth, transaction counts, TVL in DeFi, institutional inflows.
- **Whale movements:** Large holders moving assets to exchanges often precedes selling.
- **Narrative and hype:** Crypto markets are heavily narrative-driven. Memes, influencer endorsements, and viral moments can move prices independent of any fundamental value.

Crypto is driven by both fundamentals and speculation. In the short term, speculation usually dominates.

## Regulatory Landscape

Regulation varies dramatically by jurisdiction and changes frequently. What is legal today may be restricted tomorrow. Major regulatory events to monitor: SEC enforcement actions, country-level bans or approvals, stablecoin regulation, and exchange licensing requirements. Regulatory risk is systemic -- it affects the entire market, not just individual assets.
