## PROJECT SYNDICATE — PHASE 3C CLAUDE CODE KICKOFF PROMPT
## Copy everything below the line and paste it into Claude Code
## ================================================================

I'm continuing Project Syndicate. Read the CLAUDE.md file first, then CURRENT_STATUS.md and CHANGELOG.md to confirm Phase 3B is complete.

This is Phase 3C — Paper Trading Infrastructure. Phase 3 is split into 6 sub-phases:
- 3A: The Agent Thinking Cycle ← COMPLETE
- 3B: The Cold Start Boot Sequence ← COMPLETE
- **3C: Paper Trading Infrastructure** ← YOU ARE HERE
- 3D: The First Evaluation Cycle
- 3E: Personality Through Experience
- 3F: First Death, First Reproduction, First Dynasty

Work through each step in order. Use CMD commands only, never PowerShell. Always activate the .venv before any Python work.

**IMPORTANT:** Before modifying ANY existing file, create a timestamped backup in backups/. Before starting, run `python scripts/backup.py` to snapshot the current state.

---

## CONTEXT — What Is The Paper Trading Engine?

When Operator-First says "execute_trade," something real needs to happen — real market prices, simulated fills, tracked positions, live P&L. Just no real money moving.

This is the simulated execution layer. It uses **real market data in real-time** from live exchanges. The only thing that's fake is the execution — no actual orders hit the exchange. Everything else is as realistic as possible.

### Core Philosophy: "Lie As Little As Possible"

Agents that graduate from paper to live trading (Phase 8) must not be surprised by reality. If the paper engine is a toy — instant fills, no fees, no slippage — agents develop strategies that only work in fantasy-land. The simulation must punish the same mistakes the real market punishes.

### What's Real vs Simulated vs Not Simulated

**REAL (from live exchange):**
- Price data (ticker, OHLCV, bid/ask)
- Order book depth (for slippage modeling)
- Volume
- Market regime
- Fee schedules (hardcoded to match Kraken's actual rates)
- Trading pair availability

**SIMULATED (computed locally):**
- Order matching (modeled fills, not a matching engine)
- Slippage (modeled from real order book depth)
- Balance / portfolio state (tracked in our DB)
- Stop-loss and take-profit triggering

**NOT SIMULATED (documented simplifications):**
- Order book impact — at $50 positions, our orders literally wouldn't move the market. Not simulating this is *more* realistic.
- Partial fills — at our position sizes, we're always a tiny fraction of available volume. Revisit at scale.
- Exchange downtime — adds complexity for minimal learning value
- Flash crashes — synthetic crashes would be fiction, not realism
- Funding rates — we're simulating spot, not perpetual futures
- Margin/liquidation — Warden position limits prevent catastrophic exposure

---

## THE PAPER/LIVE SWITCH ARCHITECTURE

This is the most important architectural decision. Phase 8 (Go Live) must be a config change, not a rewrite.

```
ABSTRACT INTERFACE: TradeExecutionService
    
    async execute_market_order(agent_id, symbol, side, size_usd) -> OrderResult
    async execute_limit_order(agent_id, symbol, side, size_usd, price) -> OrderResult
    async cancel_order(order_id) -> CancelResult
    async get_open_orders(agent_id) -> list[Order]
    async get_positions(agent_id) -> list[Position]
    async close_position(position_id, order_type) -> CloseResult
    async get_balance(agent_id) -> Balance

IMPLEMENTATION 1: PaperTradingService (Phase 3C — this phase)
    - All execution is simulated against real market data
    - Positions tracked in our database
    - Slippage model, fee model, position monitor

IMPLEMENTATION 2: LiveTradingService (Phase 8 — future)
    - Routes orders to real exchange via ccxt
    - Same interface, different backend

CONFIGURATION:
    # In .env
    TRADING_MODE=paper  # or "live"
    
    # In code
    def get_trading_service():
        if config.trading_mode == "paper":
            return PaperTradingService()
        elif config.trading_mode == "live":
            return LiveTradingService()
```

Everything upstream — the Warden, the Action Executor, the agents — calls the same interface. They don't know or care whether they're paper trading or live. The switch is one environment variable.

---

## ORDER TYPES

### Market Order
- Fills immediately at the current **ask price** (buys) or **bid price** (sells)
- Plus simulated slippage based on order size relative to order book depth
- Plus taker fee (Kraken: 0.26%)
- Actual processing time recorded (no artificial delay)

### Limit Order
- Placed at a specific price
- **Reserves cash** from agent's available balance at placement (including estimated fees)
- Sits in the limit order monitor until market price crosses the limit
- Fills at limit price or better, plus maker fee (Kraken: 0.16%)
- May never fill if price doesn't reach the limit
- Expires after 24 hours (configurable)
- On cancel or expiry, reserved cash is released

### Stop-Loss Order
- Conditional market order attached to an open position
- When market price drops to (or below) stop price → triggers a market sell
- **Fills at the bid price at trigger time, NOT the stop price** — this is critical realism. In fast markets, your stop at $100 might fill at $97.
- Plus slippage and taker fee

### Take-Profit Order
- Conditional limit order attached to an open position
- When market price rises to take-profit level → triggers a limit sell
- Fills at the take-profit price (limit = price control)
- Plus maker fee

---

## SLIPPAGE MODEL

Most paper trading engines lie here. They fill at the exact price you see. Real markets don't work that way.

```
Class: SlippageModel

    async calculate_slippage(order_size_usd, symbol, side) -> float:
        """
        Returns slippage as a percentage (e.g., 0.001 = 0.1%).
        Based on real order book depth.
        """
        
        # 1. Fetch order book (cached — see shared price cache section)
        order_book = await get_cached_order_book(symbol)
        
        # 2. Walk the relevant side
        #    BUY → walk asks (taking liquidity)
        #    SELL → walk bids
        levels = order_book["asks"] if side == "buy" else order_book["bids"]
        
        # 3. Calculate VWAP for our order size
        remaining_usd = order_size_usd
        filled_quantity = 0
        total_cost = 0
        
        for price, quantity in levels:
            level_value_usd = price * quantity
            
            if remaining_usd <= level_value_usd:
                # This level can fill the rest
                fill_qty = remaining_usd / price
                filled_quantity += fill_qty
                total_cost += remaining_usd
                remaining_usd = 0
                break
            else:
                # Consume entire level
                filled_quantity += quantity
                total_cost += level_value_usd
                remaining_usd -= level_value_usd
        
        if remaining_usd > 0:
            # Order exceeds visible book depth — penalty slippage
            best_price = levels[0][0]
            vwap = total_cost / filled_quantity if filled_quantity > 0 else best_price
            penalty = 0.005  # 0.5% extra
            slippage_pct = abs(vwap - best_price) / best_price + penalty
        else:
            best_price = levels[0][0]
            vwap = total_cost / filled_quantity
            slippage_pct = abs(vwap - best_price) / best_price
        
        # 4. Add noise (±20%) to prevent gaming deterministic fills
        noise = random.uniform(0.8, 1.2)
        slippage_pct *= noise
        
        # 5. Floor: minimum 0.01% (even the most liquid markets have some cost)
        slippage_pct = max(slippage_pct, 0.0001)
        
        return slippage_pct
```

**Why noise?** If slippage is deterministic, agents could learn to predict fills and game the simulation. Noise keeps it honest.

---

## FEE SCHEDULE

Hardcoded to match real exchange rates:

```
Class: FeeSchedule

    EXCHANGES = {
        "kraken": {
            "maker": 0.0016,   # 0.16%
            "taker": 0.0026,   # 0.26%
        },
        "binance": {
            "maker": 0.0010,   # 0.10%
            "taker": 0.0010,   # 0.10%
        }
    }
    
    calculate_fee(order_size_usd, order_type, exchange="kraken") -> float:
        if order_type == "market":
            rate = EXCHANGES[exchange]["taker"]
        elif order_type == "limit":
            rate = EXCHANGES[exchange]["maker"]
        return order_size_usd * rate
```

---

## SHARED PRICE CACHE

Critical infrastructure. All consumers — Position Monitor, Limit Order Monitor, Context Assembler, Slippage Model — read from a single cache. One exchange fetch per symbol per 10 seconds.

```
Class: PriceCache

    TICKER_TTL = 10          # seconds
    ORDER_BOOK_TTL = 10      # seconds
    STALE_THRESHOLD = 60     # seconds — beyond this, prices are unreliable
    
    async get_ticker(symbol) -> tuple[dict, bool]:
        """Returns (ticker_data, is_fresh). is_fresh=False if stale."""
        
        cache_key = f"price:{symbol}"
        cached = redis.get(cache_key)
        
        if cached and age(cached) < TICKER_TTL:
            return json.loads(cached), True
        
        try:
            fresh = await exchange.get_ticker(symbol)
            redis.set(cache_key, json.dumps(fresh), ex=TICKER_TTL)
            return fresh, True
        except Exception as e:
            log.warning(f"Exchange fetch failed for {symbol}: {e}")
            if cached and age(cached) < STALE_THRESHOLD:
                return json.loads(cached), False  # stale but usable
            if cached:
                return json.loads(cached), False  # stale and old — flag it
            return None, False  # no data at all
    
    async get_order_book(symbol, limit=20) -> tuple[dict, bool]:
        """Same pattern as ticker but for order book data."""
        
        cache_key = f"orderbook:{symbol}"
        cached = redis.get(cache_key)
        
        if cached and age(cached) < ORDER_BOOK_TTL:
            return json.loads(cached), True
        
        try:
            fresh = await exchange.get_order_book(symbol, limit=limit)
            redis.set(cache_key, json.dumps(fresh), ex=ORDER_BOOK_TTL)
            return fresh, True
        except Exception as e:
            log.warning(f"Order book fetch failed for {symbol}: {e}")
            if cached:
                return json.loads(cached), age(cached) < STALE_THRESHOLD
            return None, False
    
    async batch_fetch_tickers(symbols: list[str]) -> dict:
        """Fetch multiple symbols efficiently. Only hits exchange for uncached."""
        
        results = {}
        to_fetch = []
        
        for symbol in symbols:
            cached = redis.get(f"price:{symbol}")
            if cached and age(cached) < TICKER_TTL:
                results[symbol] = json.loads(cached)
            else:
                to_fetch.append(symbol)
        
        # Fetch uncached symbols from exchange
        for symbol in to_fetch:
            try:
                ticker = await exchange.get_ticker(symbol)
                redis.set(f"price:{symbol}", json.dumps(ticker), ex=TICKER_TTL)
                results[symbol] = ticker
            except Exception as e:
                log.warning(f"Failed to fetch {symbol}: {e}")
                # Try stale cache
                cached = redis.get(f"price:{symbol}")
                if cached:
                    results[symbol] = json.loads(cached)
        
        return results
```

---

## AGENT BALANCE TRACKING

Each agent has a paper trading account:

```
Agent Paper Account:
    cash_balance:       float   # total USDT held
    reserved_cash:      float   # held for pending limit orders
    available_cash:     float   # cash_balance - reserved_cash
                                # (THIS is what the Warden checks for new trades)
    
    # Derived from positions
    position_value:     float   # sum of open position current values
    total_equity:       float   # cash_balance + position_value
    
    # Tracking
    initial_capital:    float   # what they started with
    realized_pnl:      float   # closed trade P&L (after fees)
    unrealized_pnl:    float   # open positions mark-to-market
    total_fees_paid:   float   # cumulative fees
    
    # Buying power (accounts for short positions)
    buying_power:      float   # available_cash - short_margin_requirement
    
    short_margin_requirement = sum(abs(p.current_value) for p in short_positions)
```

**The Warden checks `buying_power`**, not `cash_balance`, when evaluating trade requests. This prevents agents from over-leveraging through shorts or uncommitted limit orders.

---

## POSITION TRACKING

```
Position Record:
    id:                 SERIAL PRIMARY KEY
    agent_id:           INT FK → agents
    agent_name:         VARCHAR
    symbol:             VARCHAR       # "SOL/USDT"
    side:               VARCHAR       # "long" / "short"
    entry_price:        FLOAT         # VWAP entry including slippage
    current_price:      FLOAT         # last known market price
    quantity:           FLOAT         # base asset amount
    size_usd:           FLOAT         # position size at entry in USDT
    stop_loss:          FLOAT NULLABLE
    take_profit:        FLOAT NULLABLE
    unrealized_pnl:     FLOAT         # mark-to-market P&L
    unrealized_pnl_pct: FLOAT         # as percentage of entry
    fees_entry:         FLOAT         # fee paid on entry
    fees_exit:          FLOAT NULLABLE # fee paid on exit
    source_plan_id:     INT NULLABLE FK → plans
    source_opp_id:      INT NULLABLE FK → opportunities
    source_cycle_id:    INT FK → agent_cycles
    opened_at:          TIMESTAMP
    status:             VARCHAR       # open / closed / stopped_out / take_profit_hit
    closed_at:          TIMESTAMP NULLABLE
    close_price:        FLOAT NULLABLE
    realized_pnl:       FLOAT NULLABLE  # final P&L after all fees
    close_reason:       VARCHAR NULLABLE # manual / stop_loss / take_profit / agent_death
    execution_venue:    VARCHAR DEFAULT 'paper' # "paper" or "kraken" or "binance"
    created_at:         TIMESTAMP DEFAULT NOW()
```

**Multiple positions per symbol are allowed.** Each position links to a specific plan for clean P&L attribution. The sanity checker flags duplicates as a warning (concentration risk), not an error.

---

## ORDER RECORDS

Every order attempt gets a comprehensive record:

```
Order Record:
    id:                 SERIAL PRIMARY KEY
    agent_id:           INT FK → agents
    agent_name:         VARCHAR
    order_type:         VARCHAR       # market / limit / stop_loss / take_profit
    symbol:             VARCHAR
    side:               VARCHAR       # buy / sell
    requested_size_usd: FLOAT
    requested_price:    FLOAT NULLABLE # limit price (null for market)
    
    # Execution details
    fill_price:         FLOAT NULLABLE # actual fill after slippage
    fill_quantity:      FLOAT NULLABLE # base asset amount filled
    fill_value_usd:     FLOAT NULLABLE # total fill value
    slippage_pct:       FLOAT NULLABLE # slippage applied
    fee_usd:            FLOAT NULLABLE # fee charged
    fee_rate:           FLOAT NULLABLE # maker or taker rate
    
    # Market context at execution time
    market_bid:         FLOAT NULLABLE
    market_ask:         FLOAT NULLABLE
    market_spread_pct:  FLOAT NULLABLE
    market_volume_24h:  FLOAT NULLABLE
    
    # Timing
    requested_at:       TIMESTAMP
    filled_at:          TIMESTAMP NULLABLE
    processing_time_ms: INT NULLABLE    # actual processing time (not simulated delay)
    
    # Cash reservation (for limit orders)
    reserved_amount:    FLOAT NULLABLE  # cash reserved at placement
    reservation_released: BOOLEAN DEFAULT FALSE
    
    # Status
    status:             VARCHAR   # pending / filled / cancelled / expired / rejected
    rejection_reason:   VARCHAR NULLABLE
    
    # Pipeline links
    source_plan_id:     INT NULLABLE FK → plans
    source_cycle_id:    INT NULLABLE FK → agent_cycles
    warden_request_id:  VARCHAR NULLABLE   # Warden gate check reference
    position_id:        INT NULLABLE FK → positions  # resulting position
    
    # Venue
    execution_venue:    VARCHAR DEFAULT 'paper'
    
    created_at:         TIMESTAMP DEFAULT NOW()
```

---

## ORDER EXECUTION FLOW

```
OPERATOR THINKING CYCLE
    ↓ action: execute_trade
    ↓ params: {plan_id, market, direction, order_type, size_usd, stop_loss, take_profit}

ACTION EXECUTOR (Phase 3A)
    ↓ validates params
    ↓ submits trade request to Warden queue (Redis)

WARDEN TRADE GATE (Phase 1)
    ↓ same logic for paper and live — no difference
    ↓ checks: alert level, buying_power (not cash_balance), position limits
    ↓ returns: approved / rejected / held

IF APPROVED → PAPER TRADING ENGINE
    ↓
    ├── MARKET ORDER:
    │   1. Fetch current ask (buy) or bid (sell) from price cache
    │   2. Calculate slippage from cached order book
    │   3. Fill price = market_price ± slippage
    │   4. Calculate fee (taker rate)
    │   5. Convert USD size to base asset quantity: qty = size_usd / fill_price
    │   6. Create position record
    │   7. Deduct cost + fee from agent cash_balance
    │   8. Create order record with full market context
    │   9. Write transaction record for Accountant bridge
    │   10. Broadcast to Agora channel "trades"
    │   11. Backfill source cycle's outcome fields
    │
    └── LIMIT ORDER:
        1. Validate limit price is reasonable
        2. Calculate reservation: size_usd + estimated_fee
        3. Reserve cash from agent's available balance
        4. Create order record with status "pending"
        5. Order enters the Limit Order Monitor
        6. When market crosses limit → fill:
           a. Fill at limit price (or better if market gapped through)
           b. Calculate fee (maker rate)
           c. Create position record
           d. Adjust cash: deduct actual cost, release excess reservation
           e. Create transaction record for Accountant
           f. Broadcast to Agora
        7. If order expires (24h) → cancel:
           a. Status = "expired"
           b. Release reserved cash
           c. Log to agent's cycle record
```

---

## POSITION MONITOR

Runs continuously. Checks all open positions against live prices every 10 seconds. Triggers stop-losses and take-profits. Updates unrealized P&L.

```
Class: PositionMonitor

    MONITOR_INTERVAL = 10  # seconds
    HEARTBEAT_KEY = "heartbeat:position_monitor"
    
    async run():
        """Main loop with crash resilience and heartbeat."""
        log.info("Position Monitor starting")
        
        while True:
            try:
                await self.check_all_positions()
            except Exception as e:
                log.error(f"Position monitor cycle failed: {e}", exc_info=True)
                # Continue running — one bad cycle must not kill the monitor
            
            # Update heartbeat for Dead Man's Switch
            redis.set(HEARTBEAT_KEY, now().isoformat(), ex=30)
            
            await asyncio.sleep(MONITOR_INTERVAL)
    
    async check_all_positions():
        open_positions = db.get_all_open_positions()
        if not open_positions:
            return
        
        # Batch fetch prices for all symbols
        symbols = list(set(p.symbol for p in open_positions))
        
        for position in open_positions:
            ticker, is_fresh = await price_cache.get_ticker(position.symbol)
            
            if ticker is None:
                log.warning(f"No price data for {position.symbol} — skipping")
                continue
            
            if not is_fresh:
                # Stale price — update P&L display but DO NOT trigger stops/TPs
                log.warning(f"Stale price for {position.symbol} — stop/TP monitoring paused")
                await self.update_unrealized_pnl(position, ticker)
                
                # Alert if this is the first stale detection for this symbol
                stale_key = f"stale_alert:{position.symbol}"
                if not redis.exists(stale_key):
                    agora.broadcast("system-alerts",
                        f"Price feed stale for {position.symbol} — "
                        f"stop/TP monitoring paused for affected positions")
                    redis.set(stale_key, "1", ex=60)  # don't spam alerts
                continue
            
            # Fresh price — full monitoring
            current_bid = ticker["bid"]
            current_ask = ticker["ask"]
            current_price = (current_bid + current_ask) / 2  # mid for display
            
            # Update unrealized P&L
            await self.update_unrealized_pnl(position, ticker)
            
            # Check stop-loss
            if position.stop_loss:
                stop_triggered = (
                    (position.side == "long" and current_bid <= position.stop_loss) or
                    (position.side == "short" and current_ask >= position.stop_loss)
                )
                if stop_triggered:
                    await self.execute_stop_loss(position, ticker)
                    continue  # position is now closed, skip TP check
            
            # Check take-profit
            if position.take_profit:
                tp_triggered = (
                    (position.side == "long" and current_bid >= position.take_profit) or
                    (position.side == "short" and current_ask <= position.take_profit)
                )
                if tp_triggered:
                    await self.execute_take_profit(position, ticker)
    
    async update_unrealized_pnl(position, ticker):
        mid_price = (ticker["bid"] + ticker["ask"]) / 2
        position.current_price = mid_price
        
        if position.side == "long":
            position.unrealized_pnl = (mid_price - position.entry_price) * position.quantity
        else:
            position.unrealized_pnl = (position.entry_price - mid_price) * position.quantity
        
        position.unrealized_pnl_pct = (position.unrealized_pnl / position.size_usd) * 100
        position.save()
    
    async execute_stop_loss(position, ticker):
        """Stop fills as market order at BID (not stop price). This is reality."""
        
        # Acquire close lock to prevent double-close race condition
        lock_key = f"position:{position.id}:closing"
        acquired = redis.set(lock_key, "1", nx=True, ex=30)
        if not acquired:
            log.info(f"Position {position.id} already being closed")
            return
        
        try:
            fill_price = ticker["bid"] if position.side == "long" else ticker["ask"]
            
            # Apply slippage (stops in fast markets get ugly fills)
            slippage = await slippage_model.calculate_slippage(
                position.size_usd, position.symbol, 
                "sell" if position.side == "long" else "buy"
            )
            
            if position.side == "long":
                fill_price *= (1 - slippage)
            else:
                fill_price *= (1 + slippage)
            
            fee = fee_schedule.calculate_fee(position.size_usd, "market")
            
            await self.close_position(position, fill_price, fee, "stop_loss")
            
            agora.broadcast("trades",
                f"STOP-LOSS: {position.agent_name} {position.symbol} "
                f"stopped at {fill_price:.4f} (trigger: {position.stop_loss:.4f}, "
                f"P&L: ${position.realized_pnl:.2f})")
        finally:
            redis.delete(lock_key)
    
    async execute_take_profit(position, ticker):
        """Take-profit fills as limit order at TP price."""
        
        lock_key = f"position:{position.id}:closing"
        acquired = redis.set(lock_key, "1", nx=True, ex=30)
        if not acquired:
            return
        
        try:
            fill_price = position.take_profit
            fee = fee_schedule.calculate_fee(position.size_usd, "limit")
            
            await self.close_position(position, fill_price, fee, "take_profit")
            
            agora.broadcast("trades",
                f"TAKE-PROFIT: {position.agent_name} {position.symbol} "
                f"closed at {fill_price:.4f} (P&L: ${position.realized_pnl:.2f})")
        finally:
            redis.delete(lock_key)
    
    async close_position(position, fill_price, exit_fee, reason):
        """Close a position, update all records, bridge to Accountant."""
        
        # Calculate realized P&L
        if position.side == "long":
            gross_pnl = (fill_price - position.entry_price) * position.quantity
        else:
            gross_pnl = (position.entry_price - fill_price) * position.quantity
        
        total_fees = position.fees_entry + exit_fee
        realized_pnl = gross_pnl - total_fees
        
        # Update position record
        position.status = reason  # stopped_out, take_profit_hit, etc.
        position.close_price = fill_price
        position.closed_at = now()
        position.fees_exit = exit_fee
        position.realized_pnl = realized_pnl
        position.close_reason = reason
        position.save()
        
        # Update agent balance
        agent = get_agent(position.agent_id)
        if position.side == "long":
            # Return: quantity sold at fill_price minus exit fee
            agent.cash_balance += (fill_price * position.quantity) - exit_fee
        else:
            # Short close: deduct buyback cost plus exit fee
            agent.cash_balance -= (fill_price * position.quantity) + exit_fee
        
        agent.realized_pnl += realized_pnl
        agent.total_fees_paid += exit_fee
        agent.save()
        
        # Bridge to Accountant: write transaction record
        await self.write_accountant_transaction(position, realized_pnl, total_fees)
        
        # Backfill outcome on the original thinking cycle that opened this position
        await self.backfill_cycle_outcome(position)
    
    async write_accountant_transaction(position, realized_pnl, total_fees):
        """Write paper trade results into transactions table for the Accountant."""
        transaction = {
            "agent_id": position.agent_id,
            "type": "trade",
            "amount": realized_pnl,
            "fee": total_fees,
            "symbol": position.symbol,
            "details": {
                "side": position.side,
                "entry_price": position.entry_price,
                "exit_price": position.close_price,
                "quantity": position.quantity,
                "close_reason": position.close_reason,
                "source": "paper_trading",
                "execution_venue": position.execution_venue
            },
            "created_at": now()
        }
        db.insert("transactions", transaction)
    
    async backfill_cycle_outcome(position):
        """Write P&L back to the thinking cycle that opened this trade."""
        if position.source_cycle_id:
            cycle = db.get("agent_cycles", position.source_cycle_id)
            if cycle:
                cycle.outcome = f"Position closed: {position.close_reason} at {position.close_price}"
                cycle.outcome_pnl = position.realized_pnl
                cycle.save()
```

---

## LIMIT ORDER MONITOR

Separate from position monitor. Watches unfilled limit orders:

```
Class: LimitOrderMonitor

    MONITOR_INTERVAL = 10  # seconds
    HEARTBEAT_KEY = "heartbeat:limit_order_monitor"
    
    async run():
        """Main loop with crash resilience and heartbeat."""
        log.info("Limit Order Monitor starting")
        
        while True:
            try:
                await self.check_pending_orders()
            except Exception as e:
                log.error(f"Limit order monitor cycle failed: {e}", exc_info=True)
            
            redis.set(HEARTBEAT_KEY, now().isoformat(), ex=30)
            await asyncio.sleep(MONITOR_INTERVAL)
    
    async check_pending_orders():
        pending = db.get_pending_limit_orders()
        
        for order in pending:
            ticker, is_fresh = await price_cache.get_ticker(order.symbol)
            
            if ticker is None or not is_fresh:
                continue  # don't fill on stale prices
            
            current_price = ticker["ask"] if order.side == "buy" else ticker["bid"]
            
            # Check if limit price reached
            should_fill = (
                (order.side == "buy" and current_price <= order.requested_price) or
                (order.side == "sell" and current_price >= order.requested_price)
            )
            
            if should_fill:
                await self.fill_limit_order(order, ticker)
                continue
            
            # Check expiration
            if order.expires_at and now() > order.expires_at:
                await self.expire_order(order)
    
    async fill_limit_order(order, ticker):
        """Fill at limit price or better (price improvement possible)."""
        
        # Price improvement: if market gapped past our limit, we get the better price
        if order.side == "buy":
            fill_price = min(order.requested_price, ticker["ask"])
        else:
            fill_price = max(order.requested_price, ticker["bid"])
        
        quantity = order.requested_size_usd / fill_price
        fee = fee_schedule.calculate_fee(order.requested_size_usd, "limit")
        actual_cost = (fill_price * quantity) + fee
        
        # Create position
        position = create_position(
            agent_id=order.agent_id,
            symbol=order.symbol,
            side="long" if order.side == "buy" else "short",
            entry_price=fill_price,
            quantity=quantity,
            size_usd=order.requested_size_usd,
            stop_loss=None,  # agent sets these via adjust_position
            take_profit=None,
            fees_entry=fee,
            source_plan_id=order.source_plan_id,
            source_cycle_id=order.source_cycle_id,
            execution_venue="paper"
        )
        
        # Update agent balance: release reservation, deduct actual cost
        agent = get_agent(order.agent_id)
        agent.reserved_cash -= order.reserved_amount
        agent.cash_balance -= actual_cost
        agent.total_fees_paid += fee
        agent.save()
        
        # Update order record
        order.status = "filled"
        order.fill_price = fill_price
        order.fill_quantity = quantity
        order.fill_value_usd = fill_price * quantity
        order.fee_usd = fee
        order.filled_at = now()
        order.position_id = position.id
        order.reservation_released = True
        order.save()
        
        # Agora broadcast
        agora.broadcast("trades",
            f"LIMIT FILLED: {order.agent_name} {order.side} {order.symbol} "
            f"at {fill_price:.4f} (limit: {order.requested_price:.4f}, "
            f"size: ${order.requested_size_usd:.2f})")
    
    async expire_order(order):
        """Cancel expired limit order and release reserved cash."""
        
        order.status = "expired"
        order.reservation_released = True
        order.save()
        
        agent = get_agent(order.agent_id)
        agent.reserved_cash -= order.reserved_amount
        agent.save()
        
        log.info(f"Limit order {order.id} expired for {order.agent_name}")
```

---

## SHORT SELLING

Agents can go short (bet prices go down). Simplified spot simulation:

```
SHORT POSITION MECHANICS:

Entry:
    - Agent "sells" at current bid price (minus slippage)
    - Cash increases by sale proceeds
    - Short position tracked (agent owes the asset)
    - Margin requirement = current value of short position
    - Buying power reduced by margin requirement

P&L While Open:
    - unrealized_pnl = (entry_price - current_price) * quantity
    - Price DOWN → profit / Price UP → loss

Exit:
    - Agent "buys back" at current ask price (plus slippage)
    - Cash decreases by buyback cost
    - Margin requirement released

Stop-Loss on Shorts:
    - Triggers when price goes UP past the stop
    - Opposite of long stops

No margin calls / forced liquidation in Phase 3C.
Warden position limits prevent catastrophic exposure.
```

---

## EQUITY SNAPSHOTS (For Sharpe Ratio)

The Accountant needs daily returns for Sharpe ratio calculation. We need a clean time-series of equity values.

```
Class: EquitySnapshotService

    SNAPSHOT_INTERVAL = 300  # every 5 minutes
    
    async take_snapshots():
        """Snapshot total equity for every agent with capital."""
        
        agents = get_agents_with_capital()
        for agent in agents:
            positions = get_open_positions(agent.id)
            position_value = sum(p.current_price * p.quantity for p in positions)
            
            snapshot = {
                "agent_id": agent.id,
                "equity": agent.cash_balance + position_value,
                "cash_balance": agent.cash_balance,
                "position_value": position_value,
                "snapshot_at": now()
            }
            db.insert("agent_equity_snapshots", snapshot)
    
    async get_daily_returns(agent_id, days=30) -> list[float]:
        """Calculate daily returns from snapshots for Sharpe ratio."""
        
        # Get last snapshot of each day
        daily_equities = db.query("""
            SELECT DISTINCT ON (DATE(snapshot_at)) 
                DATE(snapshot_at) as date, equity
            FROM agent_equity_snapshots
            WHERE agent_id = :agent_id
              AND snapshot_at > NOW() - INTERVAL ':days days'
            ORDER BY DATE(snapshot_at), snapshot_at DESC
        """, agent_id=agent_id, days=days)
        
        # Calculate daily returns
        returns = []
        for i in range(1, len(daily_equities)):
            prev = daily_equities[i-1].equity
            curr = daily_equities[i].equity
            if prev > 0:
                returns.append((curr - prev) / prev)
        
        return returns
```

---

## PORTFOLIO CONCENTRATION MONITOR

Surfaces aggregate exposure data without blocking trades. Blocking logic comes in Phase 3D.

```
Class: ConcentrationMonitor

    CONCENTRATION_THRESHOLD = 0.40  # 40% of total deployed capital
    
    async check():
        all_positions = db.get_all_open_positions()
        if not all_positions:
            return
        
        total_deployed = sum(p.size_usd for p in all_positions)
        if total_deployed == 0:
            return
        
        by_symbol = {}
        for p in all_positions:
            by_symbol.setdefault(p.symbol, []).append(p)
        
        for symbol, positions in by_symbol.items():
            exposure = sum(p.size_usd for p in positions)
            concentration = exposure / total_deployed
            
            if concentration > CONCENTRATION_THRESHOLD:
                agora.broadcast("risk-flags",
                    f"CONCENTRATION: {concentration:.0%} of deployed capital "
                    f"in {symbol} across {len(positions)} agent(s)")
```

---

## SANITY CHECKER

Periodic reconciliation to catch state drift:

```
Class: PaperTradingSanityChecker

    INTERVAL = 300  # every 5 minutes
    
    async run_all():
        await self.check_cash_balances()
        await self.check_equity_reconciliation()
        await self.check_orphaned_positions()
        await self.check_duplicate_positions()
        await self.check_stale_reservations()
        await concentration_monitor.check()
        await equity_snapshot_service.take_snapshots()
    
    async check_cash_balances():
        """No agent should have negative cash."""
        for agent in get_agents_with_capital():
            if agent.cash_balance < -0.01:  # penny tolerance
                log.critical(f"{agent.name} negative cash: ${agent.cash_balance:.2f}")
                agora.broadcast("system-alerts",
                    f"CRITICAL: {agent.name} has negative cash balance")
                # Don't auto-fix — this indicates a bug
    
    async check_equity_reconciliation():
        """Total equity should match cash + positions."""
        for agent in get_agents_with_capital():
            positions = get_open_positions(agent.id)
            position_value = sum(p.current_price * p.quantity 
                               for p in positions if p.side == "long")
            position_value -= sum(p.current_price * p.quantity 
                                for p in positions if p.side == "short")
            
            expected = agent.cash_balance + position_value
            if abs(expected - agent.total_equity) > 0.01:
                log.warning(f"{agent.name} equity drift: "
                           f"expected={expected:.2f}, recorded={agent.total_equity:.2f}")
                agent.total_equity = expected
                agent.save()
    
    async check_orphaned_positions():
        """Positions for dead/inactive agents should have been inherited."""
        orphans = db.query("""
            SELECT p.* FROM positions p 
            JOIN agents a ON p.agent_id = a.id
            WHERE p.status = 'open' AND a.status NOT IN ('active', 'hibernating')
        """)
        for orphan in orphans:
            log.warning(f"Orphaned position: {orphan.id} for agent {orphan.agent_id}")
            agora.broadcast("system-alerts",
                f"Orphaned position detected: {orphan.symbol} for inactive agent")
    
    async check_duplicate_positions():
        """Flag same agent with multiple positions on same symbol+side."""
        dupes = db.query("""
            SELECT agent_id, symbol, side, COUNT(*) as cnt
            FROM positions WHERE status = 'open'
            GROUP BY agent_id, symbol, side
            HAVING COUNT(*) > 1
        """)
        for dupe in dupes:
            log.warning(f"Duplicate positions: agent {dupe.agent_id} "
                       f"has {dupe.cnt} {dupe.side} positions on {dupe.symbol}")
    
    async check_stale_reservations():
        """Limit orders cancelled/expired but reservation not released."""
        stale = db.query("""
            SELECT * FROM orders
            WHERE status IN ('cancelled', 'expired')
              AND reservation_released = FALSE
              AND reserved_amount > 0
        """)
        for order in stale:
            log.warning(f"Stale reservation: order {order.id}, "
                       f"${order.reserved_amount:.2f} still reserved")
            # Auto-fix: release the reservation
            agent = get_agent(order.agent_id)
            agent.reserved_cash -= order.reserved_amount
            agent.save()
            order.reservation_released = True
            order.save()
```

---

## AGENT DEATH WITH OPEN POSITIONS

Connects to existing Genesis position inheritance from Phase 1:

```
AGENT DEATH FLOW:

1. Genesis marks agent for termination
2. All pending limit orders cancelled → reservations released
3. All open positions transferred to inherited_positions table
4. Genesis has 24 hours to close inherited positions
5. Close goes through paper trading engine (slippage + fees apply)
6. Realized P&L from inherited closes goes to treasury
7. Agent's remaining cash balance returned to treasury
8. All records preserved for post-mortem analysis
```

---

## DATABASE SCHEMA

Create a new Alembic migration for Phase 3C:

**New table: `positions`**
(Schema exactly as defined in Position Tracking section above)

**New table: `orders`**
(Schema exactly as defined in Order Records section above)

**New table: `agent_equity_snapshots`**
```
id              SERIAL PRIMARY KEY
agent_id        INT FK → agents
equity          FLOAT
cash_balance    FLOAT
position_value  FLOAT
snapshot_at     TIMESTAMP DEFAULT NOW()

INDEX: (agent_id, snapshot_at)
```

**Updates to `agents` table:**
```
cash_balance            FLOAT DEFAULT 0.0
reserved_cash           FLOAT DEFAULT 0.0
total_equity            FLOAT DEFAULT 0.0
realized_pnl            FLOAT DEFAULT 0.0
unrealized_pnl          FLOAT DEFAULT 0.0
total_fees_paid         FLOAT DEFAULT 0.0
position_count          INT DEFAULT 0
```

Run: `alembic revision --autogenerate -m "phase_3c_paper_trading"`
Then: `alembic upgrade head`

---

## IMPLEMENTATION STEPS

### STEP 1 — Verify Phase 3B Foundation

Before building anything, confirm:
- .venv activates and all dependencies are importable
- PostgreSQL database is accessible with all Phase 3B tables
- Redis/Memurai responds to PING
- Phase 3B modules work (boot sequence, orientation, pipeline)
- Tests pass: `python -m pytest tests/ -v`

If anything is broken, fix it before proceeding.

---

### STEP 2 — Add Phase 3C Dependencies

Check requirements.txt and add if not present:
- No new external dependencies expected — ccxt, redis, sqlalchemy, numpy should already be installed

Run: `pip install -r requirements.txt`

---

### STEP 3 — Database Migration

Create and run the Alembic migration for the three new tables (positions, orders, agent_equity_snapshots) and agent table updates described above.

---

### STEP 4 — Price Cache (src/common/price_cache.py)

Implement the shared price cache as specified above. This is foundational — everything else depends on it.

- Redis-backed with configurable TTL (10s for tickers, 10s for order books)
- Stale price detection (60-second threshold)
- Batch fetch for multiple symbols
- Graceful fallback to stale cache on exchange errors
- Logging of all cache misses and exchange failures

---

### STEP 5 — Fee Schedule (src/trading/fee_schedule.py)

Implement the fee schedule module. Simple but important to get right:

- Kraken and Binance fee structures
- Maker vs taker fee selection based on order type
- Configurable via SyndicateConfig for easy updates

---

### STEP 6 — Slippage Model (src/trading/slippage_model.py)

Implement the order-book-based slippage model:

- Fetch order book from price cache
- Walk the book to calculate VWAP for order size
- Noise factor (±20%)
- Floor of 0.01%
- Penalty for exceeding visible book depth
- Logging of calculated slippage per trade

---

### STEP 7 — Trade Execution Interface (src/trading/execution_service.py)

Create the abstract TradeExecutionService interface and the PaperTradingService implementation:

- Abstract base class with all methods defined
- PaperTradingService implementing market orders, limit orders, position management
- Position close with Redis locking to prevent double-close
- Cash balance and reserved cash management
- Buying power calculation (accounts for shorts)
- Order record creation with full market context
- Accountant bridge: write transactions on position close
- Cycle outcome backfill on position close

---

### STEP 8 — Position Monitor (src/trading/position_monitor.py)

Implement as specified above:

- 10-second loop with crash resilience (try/except around full cycle)
- Heartbeat reporting to Dead Man's Switch
- Stale price detection — pause stop/TP monitoring on stale data
- Stop-loss execution at bid price with slippage
- Take-profit execution at limit price
- Redis lock on position close to prevent race conditions
- Unrealized P&L updates on every cycle
- Agora broadcasts for all stop/TP triggers

---

### STEP 9 — Limit Order Monitor (src/trading/limit_order_monitor.py)

Implement as specified above:

- 10-second loop with crash resilience and heartbeat
- Stale price check — don't fill on stale data
- Limit fill with price improvement
- Order expiration (24-hour default)
- Cash reservation release on fill, cancel, or expiry
- Agora broadcasts for fills

---

### STEP 10 — Equity Snapshot Service (src/trading/equity_snapshots.py)

Implement as specified:

- Snapshot every 5 minutes for all agents with capital
- Store in agent_equity_snapshots table
- Daily return calculation method for Sharpe ratio
- Called by the sanity checker on its 5-minute cycle

---

### STEP 11 — Sanity Checker (src/trading/sanity_checker.py)

Implement all checks:

- Negative cash detection
- Equity reconciliation with auto-correct
- Orphaned position detection
- Duplicate position flagging (warning, not error)
- Stale reservation cleanup
- Portfolio concentration monitoring
- Calls equity snapshot service
- Runs every 5 minutes

---

### STEP 12 — Update Warden Integration

Modify the Warden's trade gate (src/risk/warden.py) to check **buying_power** instead of **cash_balance** when evaluating trade requests. Buying power = available_cash - short margin requirement.

This is a modification to the risk layer — verify the change is minimal and focused.

**IMPORTANT:** This is a risk layer modification. Keep the change surgical — only the balance check logic changes. All other Warden behavior stays identical. Run Warden tests after the change.

---

### STEP 13 — Update Action Executor Integration

Modify `src/agents/action_executor.py` to route Operator trade actions through the `TradeExecutionService` instead of the Phase 3A placeholder:

- `execute_trade` → `trading_service.execute_market_order()` or `execute_limit_order()`
- `close_position` → `trading_service.close_position()`
- `adjust_position` → update stop_loss/take_profit on position record; if adding size, route through Warden + trading service
- `hedge` → treated as a new trade through the standard flow, linked to the hedged position

Remove the Phase 3A placeholder mock response for trade actions.

---

### STEP 14 — Update Dead Man's Switch

Add the Position Monitor and Limit Order Monitor heartbeats to the Dead Man's Switch watchlist. If either monitor misses a heartbeat for 60 seconds, alert.

---

### STEP 15 — Trade History API Endpoint

Add to the FastAPI app (src/console/ or wherever the Phase 2D API lives):

```
GET /api/trades
    Query params: agent_id, symbol, date_from, date_to, status, page, per_page
    Returns: paginated list of order records with P&L

GET /api/positions
    Query params: agent_id, symbol, status (open/closed/all)
    Returns: position records with current P&L

GET /api/portfolio
    Returns: aggregate portfolio view — total equity, positions by symbol,
             concentration percentages, buying power per agent
```

Add a "Trades" view to the dashboard templates showing recent trades with P&L coloring (green/red).

---

### STEP 16 — Process Runner Updates

**scripts/run_trading.py:**
- Starts Position Monitor and Limit Order Monitor as async tasks
- Both run in the same process (they share the price cache)
- Includes heartbeat reporting and graceful shutdown

**Update scripts/run_all.py:**
- Add the trading monitors to the list of managed processes
- Monitor their health alongside Genesis and Warden

---

### STEP 17 — Tests

**tests/test_price_cache.py:**
- Test cache hit returns cached data
- Test cache miss fetches from exchange
- Test stale detection after threshold
- Test batch fetch only queries uncached symbols
- Test graceful fallback when exchange is unreachable

**tests/test_slippage_model.py:**
- Test slippage increases with order size
- Test slippage has noise (same inputs produce different outputs)
- Test minimum floor (0.01%)
- Test penalty for exceeding book depth
- Test buy vs sell uses correct side of book

**tests/test_fee_schedule.py:**
- Test market order uses taker rate
- Test limit order uses maker rate
- Test Kraken vs Binance rates

**tests/test_paper_trading.py:**
- Test market buy creates position and deducts cash + fee
- Test market sell (short) creates position and adds cash
- Test limit buy reserves cash, fills when price crosses, releases excess
- Test limit order expiration releases reservation
- Test position close calculates correct P&L
- Test stop-loss triggers at bid price with slippage
- Test take-profit triggers at limit price
- Test close locking prevents double-close
- Test buying power accounts for short positions and reservations
- Test Warden rejects trade when buying power insufficient
- Test USD-to-quantity conversion at fill price
- Test multiple positions per symbol allowed

**tests/test_position_monitor.py:**
- Test unrealized P&L updates correctly for long and short
- Test stop-loss triggers for long (price drops below stop)
- Test stop-loss triggers for short (price rises above stop)
- Test take-profit triggers correctly
- Test stale price pauses stop/TP monitoring
- Test monitor continues after exception in one position
- Test heartbeat is updated each cycle

**tests/test_limit_order_monitor.py:**
- Test fill when price crosses limit
- Test price improvement (fill better than limit)
- Test no fill on stale prices
- Test expiration after 24 hours
- Test reservation released on fill and on expiry

**tests/test_sanity_checker.py:**
- Test negative cash detection
- Test equity reconciliation auto-correct
- Test orphaned position detection
- Test stale reservation cleanup
- Test concentration warning at threshold

**tests/test_equity_snapshots.py:**
- Test snapshot creation for agent with positions
- Test daily return calculation from snapshots
- Test handling of agent with no snapshots

**tests/test_accountant_bridge.py:**
- Test transaction record created on position close
- Test transaction has correct P&L and fee data
- Test Accountant can read paper trading transactions

Run all: `python -m pytest tests/ -v`

---

### STEP 18 — Configuration Updates

Add to SyndicateConfig:

```python
# Phase 3C: Paper Trading
trading_mode: str = "paper"  # "paper" or "live"
price_cache_ticker_ttl: int = 10
price_cache_orderbook_ttl: int = 10
stale_price_threshold: int = 60
position_monitor_interval: int = 10
limit_order_monitor_interval: int = 10
sanity_check_interval: int = 300
equity_snapshot_interval: int = 300
default_limit_order_expiry_hours: int = 24
min_slippage_pct: float = 0.0001
slippage_noise_range: float = 0.2  # ±20%
book_depth_penalty_pct: float = 0.005
concentration_warning_threshold: float = 0.40
```

Update .env.example with new variables.

---

### STEP 19 — Update CLAUDE.md

Add Phase 3C components to the architecture section:
- Paper Trading Engine (src/trading/)
- Price Cache (src/common/price_cache.py)
- Slippage Model, Fee Schedule
- Position Monitor, Limit Order Monitor
- Equity Snapshot Service
- Sanity Checker with Concentration Monitor
- Trade Execution Interface (abstract — paper/live switch)
- Trade History API endpoints

Update Phase Roadmap to show Phase 3C as COMPLETE.

---

### STEP 20 — Update CHANGELOG.md and CURRENT_STATUS.md

Log everything built in this session.

---

### STEP 21 — Git Commit and Push

```
git add .
git commit -m "Phase 3C: Paper Trading Infrastructure — realistic simulation engine"
git push origin main
```

---

## DESIGN DECISIONS (Reference for Claude Code)

These decisions were made in the War Room (Claude.ai chat) and are final:

1. **Simulation realism is maximized.** Real prices, real spreads, real order book depth for slippage, real fee schedules. Agents must not be surprised by reality when going live.
2. **Paper/live switch is one env variable.** Abstract TradeExecutionService interface with PaperTradingService and future LiveTradingService implementations. Upstream code doesn't know or care.
3. **Slippage model uses real order book** with ±20% noise to prevent gaming and 0.01% floor.
4. **Stop-losses fill at BID price (not stop price)** plus slippage. This is how real stops work.
5. **Shared price cache** (Redis, 10s TTL) for all consumers. One exchange fetch per symbol per 10 seconds max.
6. **Stale price threshold of 60 seconds.** Beyond this, stop/TP monitoring pauses. No false triggers on stale data.
7. **Reserved cash for pending limit orders.** Warden checks buying_power (available_cash minus short margin and reservations), not raw cash_balance.
8. **Position close locking** via Redis to prevent double-close race conditions between monitors and agent actions.
9. **Monitors report heartbeats** to Dead Man's Switch. Crash resilience via try/except around each cycle.
10. **Accountant bridge:** Paper trades write to transactions table so existing Accountant works unchanged. Same format for live trades later.
11. **Equity snapshots every 5 minutes** for Sharpe ratio calculation. Daily return = end-of-day equity vs start-of-day.
12. **execution_venue field** (not is_paper boolean) on orders and positions. Values: "paper", "kraken", "binance". Scales to multi-exchange.
13. **Multiple positions per symbol allowed.** Each linked to its source plan. Sanity checker flags duplicates as warnings, not errors.
14. **No artificial execution delay.** Record actual processing time instead. Fake latency adds no value.
15. **Portfolio concentration monitoring.** Alert at 40% exposure to any single symbol. Logging only — blocking comes in Phase 3D.
16. **No partial fills, no margin calls, no funding rates** in Phase 3C. Documented simplifications appropriate for our position sizes.

---

## KNOWN SIMPLIFICATIONS (Not Bugs)

These are deliberate scope limits for Phase 3C, documented so future phases know what to revisit:

- **No partial fills:** At $20-50 position sizes, we always fill completely against deep order books. Revisit when scaling to larger positions.
- **No order book impact:** Our orders don't move the market. At our sizes, this is accurate.
- **No exchange downtime simulation:** Handled by stale price detection instead.
- **No flash crash simulation:** Would be building fiction, not realism.
- **No funding rates:** Spot trading only. Add with perpetual futures support.
- **No margin calls / forced liquidation on shorts:** Warden position limits prevent catastrophic exposure.
- **No artificial execution latency:** Removed as cosmetic. Real processing time is recorded.

---

Before you start, confirm you've read CLAUDE.md and the current project state. Then proceed through each step in order. Ask me if anything is unclear.
