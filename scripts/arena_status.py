"""Quick Arena status check."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"), override=True)

from sqlalchemy import create_engine, text
from src.common.config import config
engine = create_engine(config.database_url)
with engine.connect() as conn:
    agents = conn.execute(text("SELECT id, name, type, status FROM agents WHERE id != 0 ORDER BY id")).fetchall()
    cycles = conn.execute(text("SELECT count(*) FROM agent_cycles")).scalar()
    msgs = conn.execute(text("SELECT count(*) FROM messages")).scalar()
    cost = conn.execute(text("SELECT COALESCE(SUM(api_cost_usd), 0) FROM agent_cycles")).scalar()
    hb = conn.execute(text("SELECT last_heartbeat_at, active_agent_count FROM system_state LIMIT 1")).fetchone()

print("=== ARENA STATUS ===")
print(f"Cycles: {cycles} | Messages: {msgs} | API Cost: ${float(cost):.4f}")
print(f"Active Count: {hb[1]} | Heartbeat: {hb[0]}")
print()
for a in agents:
    print(f"  {a[1]:20s}  {a[2]:12s}  {a[3]}")
