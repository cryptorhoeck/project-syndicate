# Arena Monitoring Checklist

## Daily Check-In (5 minutes)

### Dashboard Quick Look
- [ ] Dashboard loads at http://localhost:8000
- [ ] All 5 agents showing as active (or expected state)
- [ ] No system alerts (alert status = GREEN)
- [ ] Treasury balance makes sense (started at $500)

### Agora Activity
- [ ] Messages flowing in agent-activity channel
- [ ] Scouts are broadcasting opportunities (or going idle with reasoning)
- [ ] Pipeline is moving (opportunities -> plans -> reviews)

### Financial Health
- [ ] Total API cost for last 24h < $5 (budget: $2.50/day agents + Genesis overhead)
- [ ] If any trades executed: P&L displayed, fees tracked
- [ ] No negative cash balances on any agent

### Process Health
- [ ] All processes running (check run_arena.py console output)
- [ ] No error spam in logs
- [ ] Dead Man's Switch hasn't triggered

### Red Flags (Investigate Immediately)
- All agents going idle every cycle (pipeline frozen)
- API costs spiking above $10/day (runaway thinking)
- Warden alerts (Yellow/Red)
- Any process repeatedly crashing and restarting
- Dashboard showing stale data (not updating)

## Day 10 — Health Check
- [ ] Genesis Day-10 health check runs for all Gen 1 agents
- [ ] All agents passed (or Genesis flagged issues)
- [ ] Review any flagged agents — are they actually broken or just cautious?

## Day 21 — First Evaluation
- [ ] Evaluations trigger for all 5 agents
- [ ] Review results: who survived, who's on probation, who died
- [ ] Post-mortems generated for any dead agents
- [ ] Capital reallocation happened
- [ ] If any role gap: emergency spawn triggered
- [ ] Review daily report email (if SMTP configured)

## Success Criteria
After 21 days, the Arena is a success if:
- [ ] At least 2 of 5 agents survived first evaluation
- [ ] The pipeline produced at least 1 executed trade
- [ ] No system crashes requiring manual restart
- [ ] Total API cost stayed under $75
- [ ] Daily reports were generated (in DB, even if email not configured)
- [ ] The dashboard showed real data flowing throughout
