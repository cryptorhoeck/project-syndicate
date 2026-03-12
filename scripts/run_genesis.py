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
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.genesis.genesis_runner import main

if __name__ == "__main__":
    asyncio.run(main())
