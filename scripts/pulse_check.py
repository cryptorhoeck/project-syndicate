"""Detailed Arena pulse check."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"), override=True)

from sqlalchemy import create_engine, text
from src.common.config import config
engine = create_engine(config.database_url)
with engine.connect() as conn:
    per_agent = conn.execute(text("""
        SELECT a.name, a.type, count(*), COALESCE(SUM(ac.api_cost_usd), 0),
               a.thinking_budget_daily, a.thinking_budget_used_today
        FROM agent_cycles ac
        JOIN agents a ON a.id = ac.agent_id
        GROUP BY a.name, a.type, a.thinking_budget_daily, a.thinking_budget_used_today
        ORDER BY SUM(ac.api_cost_usd) DESC
    """)).fetchall()

    stats = conn.execute(text(
        "SELECT MIN(created_at), MAX(created_at), count(*), COALESCE(SUM(api_cost_usd),0) FROM agent_cycles"
    )).fetchone()
    elapsed_hrs = (stats[1] - stats[0]).total_seconds() / 3600 if stats[0] and stats[1] else 1
    total_cycles = stats[2]
    total_cost = float(stats[3])

    msgs = conn.execute(text(
        "SELECT channel, agent_name, message_type, content, timestamp FROM messages ORDER BY timestamp DESC LIMIT 15"
    )).fetchall()

    actions = conn.execute(text(
        "SELECT action_type, count(*) FROM agent_cycles GROUP BY action_type ORDER BY count(*) DESC"
    )).fetchall()

    hb = conn.execute(text("SELECT last_heartbeat_at, active_agent_count FROM system_state LIMIT 1")).fetchone()

print(f"=== ARENA PULSE CHECK ===")
print(f"Cycles: {total_cycles} | Cost: ${total_cost:.4f} | Elapsed: {elapsed_hrs:.1f}h | Rate: ${total_cost/elapsed_hrs:.2f}/hr")
print(f"Heartbeat: {hb[0]} | Active: {hb[1]}")
print()
print(f"{'Agent':20s} {'Role':12s} {'Cycles':>6s} {'Cost':>8s} {'Budget':>7s} {'Used':>8s} {'Left':>8s}")
print("-" * 73)
for r in per_agent:
    budget = float(r[4]) if r[4] else 0
    used = float(r[5]) if r[5] else 0
    left = budget - used
    print(f"{r[0]:20s} {r[1]:12s} {r[2]:6d} ${float(r[3]):7.4f} ${budget:6.2f} ${used:7.4f} ${left:7.4f}")

print()
print("=== ACTION DISTRIBUTION ===")
for a in actions:
    print(f"  {a[0]:30s} {a[1]:4d}")

print()
print("=== LATEST AGORA (newest last) ===")
for m in reversed(msgs):
    ts = str(m[4])[11:19]
    content = (m[3] or "")[:100]
    print(f"[{ts}] #{m[0]:16s} {m[1]:20s} {content}")
