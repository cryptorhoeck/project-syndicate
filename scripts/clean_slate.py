"""Clean slate reset for Arena restart."""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, ".env"), override=True)

import psycopg2
from src.common.config import config

conn = psycopg2.connect(config.database_url)
conn.autocommit = True
cur = conn.cursor()

# Kill any stuck backends on this database (except ourselves)
cur.execute("""
    SELECT pg_terminate_backend(pid)
    FROM pg_stat_activity
    WHERE datname = current_database()
    AND pid != pg_backend_pid()
    AND state != 'idle'
""")
killed = cur.fetchall()
print(f"Terminated {len(killed)} stuck backends")

# Now do the reset in autocommit mode (TRUNCATE needs it for speed)
tables = [
    'agent_cycles', 'agent_equity_snapshots', 'agent_long_term_memory',
    'agent_reflections', 'agent_relationships', 'behavioral_profiles',
    'boot_sequence_log', 'critic_accuracy', 'divergence_scores',
    'dynasties', 'evaluations', 'gaming_flags', 'inherited_positions',
    'intel_endorsements', 'intel_signals', 'library_contributions',
    'library_views', 'lineage', 'memorials', 'messages', 'opportunities',
    'orders', 'plans', 'positions', 'post_mortems', 'rejection_tracking',
    'reputation_transactions', 'review_assignments', 'review_requests',
    'study_history', 'transactions', 'agora_read_receipts',
]

for table in tables:
    try:
        cur.execute(f"TRUNCATE TABLE {table} CASCADE")
        print(f"  truncated: {table}")
    except Exception as e:
        print(f"  skip: {table} ({e})")

# Delete non-Genesis agents
cur.execute("DELETE FROM agents WHERE id != 0")
print(f"  deleted agents: {cur.rowcount}")

# Reset system_state
cur.execute("""
    UPDATE system_state SET
        total_treasury = 500.0,
        peak_treasury = 500.0,
        active_agent_count = 0,
        last_heartbeat_at = NULL,
        current_regime = 'unknown'
""")
print("  system_state reset")

# Reset Genesis treasury
cur.execute("""
    UPDATE agents SET
        capital_allocated = 500.0,
        capital_current = 500.0
    WHERE id = 0
""")
print("  genesis treasury reset to $500")

cur.close()
conn.close()
print("\nClean slate complete.")
