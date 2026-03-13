# DeFi Protocols

## What DeFi Is

Decentralized Finance (DeFi) is financial infrastructure built on smart contracts -- self-executing code deployed on blockchains. It replicates traditional financial services (lending, borrowing, trading, insurance) without banks, brokers, or intermediaries.

DeFi is permissionless -- no KYC, no application process, no business hours. This is both its greatest strength and greatest risk: no safety net, no customer support, no recourse if something goes wrong.

## Lending and Borrowing

Protocols like Aave and Compound operate autonomous money markets.

**Lending:** Deposit assets into a pool, earn interest from borrowers. Rates are algorithmic -- they rise with utilization.

**Borrowing:** Deposit collateral, borrow against it. All DeFi borrowing is over-collateralized (typically 150%+). If collateral value drops below the liquidation threshold, the protocol automatically sells it. Liquidation is instant and not negotiable.

## Liquidity Pools

Instead of order books with bids and asks, most DEXes use liquidity pools -- reserves of paired assets (e.g., ETH + USDC) deposited by liquidity providers (LPs).

Traders swap against the pool. LPs earn a share of swap fees. The pool's asset ratio shifts with every trade, adjusting the price.

## Automated Market Makers (AMMs)

AMMs are the mathematical engines behind liquidity pools. The most common model uses the constant product formula: **x * y = k**, where x and y are the quantities of each asset and k is a constant.

As one asset is bought from the pool, its quantity decreases and its price automatically increases. This creates a smooth pricing curve without needing any external price feed for execution. The simplicity is elegant but comes with inherent costs, notably impermanent loss.

## Impermanent Loss

When you provide liquidity to a pool and the price ratio of the two assets changes, the AMM rebalances your position. You end up with more of the asset that decreased in value and less of the one that increased.

Compared to simply holding the assets, you have less total value. This gap is called impermanent loss. It is called "impermanent" because it reverses if prices return to their original ratio. In practice, they often don't, making the loss very permanent.

The trading fees you earn as an LP may or may not compensate for impermanent loss. For volatile pairs, they frequently don't. For stable pairs (USDC/USDT), impermanent loss is minimal.

## Yield Farming

Yield farming is the practice of moving capital between protocols to maximize returns. Protocols offer token incentives (governance tokens, reward tokens) to attract liquidity. These incentives create high APYs that draw capital.

The cycle: protocol launches incentives -> APY is high -> capital floods in -> APY drops -> farmers leave for the next opportunity. High advertised yields are almost always temporary and frequently come with hidden risks -- token price depreciation, smart contract risk, or sudden changes to incentive structures.

Treat extreme yields (100%+ APY) with skepticism. They are either temporary, denominated in tokens that will likely depreciate, or masking genuine risk.

## Staking

Staking involves locking tokens to help secure a Proof of Stake network. In return, you earn protocol rewards, typically 3-8% annually for major networks.

Lower risk than farming but not risk-free. Tokens are illiquid during lock-up, and you remain exposed to the staked asset's price. Liquid staking derivatives (stETH, mSOL) offer tradeable tokens but add smart contract risk.

## Smart Contract Risk

The defining risk of DeFi. Smart contracts are immutable code managing real value. If the code has a bug, an exploit, or an unforeseen interaction with another protocol, funds can be drained permanently.

Even audited contracts have been exploited -- audits reduce risk but do not eliminate it. The total value lost to DeFi exploits is in the billions. Every interaction with a DeFi protocol is an implicit bet that its code is flawless. It often isn't.

## Oracle Dependencies

DeFi protocols need external price data to function -- for liquidations, collateral valuations, and settlements. This data comes from oracles (Chainlink, Pyth, etc.).

If an oracle reports an incorrect price, protocols act on that incorrect price. Oracle manipulation is a proven attack vector -- flash loan attacks that temporarily distort prices on low-liquidity pools have been used to trigger false liquidations and drain protocols. Oracle quality is a fundamental infrastructure dependency.

## Bridges

Bridges transfer assets between blockchains. They typically lock assets on one chain and mint equivalent tokens on another.

Bridges are historically the most exploited component in all of DeFi. They hold concentrated value, have complex multi-chain logic, and represent a single point of failure. The Ronin bridge hack ($625M), Wormhole ($320M), and Nomad ($190M) are just the most prominent examples. Interact with bridges only when necessary and only with well-established ones. Keep bridge exposure to the minimum required.
