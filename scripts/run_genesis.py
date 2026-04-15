"""
Project Syndicate — Run Genesis

Standalone script that starts the Genesis main loop.
Usage: python scripts/run_genesis.py
"""

__version__ = "0.2.0"

import asyncio
import os
import sys

# Ensure project root is on sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, ".env"), override=True)

from src.genesis.genesis_runner import main

if __name__ == "__main__":
    asyncio.run(main())
