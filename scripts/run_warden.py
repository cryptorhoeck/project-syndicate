"""
Project Syndicate — Run Warden

Standalone script that starts the Warden as its own process.
Independent of Genesis — if Genesis dies, Warden keeps running.
Usage: python scripts/run_warden.py
"""

__version__ = "0.2.0"

import asyncio
import os
import sys

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.risk.warden_runner import main

if __name__ == "__main__":
    asyncio.run(main())
