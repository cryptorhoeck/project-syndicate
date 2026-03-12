"""
Project Syndicate — Web Frontend Runner

Start the Mission Control dashboard.
Usage: python scripts/run_web.py
Or:    uvicorn src.web.app:app --host 0.0.0.0 --port 8000 --reload
"""

__version__ = "0.6.0"

import sys
import os

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    """Start the web frontend."""
    # Verify Python version
    if sys.version_info < (3, 12):
        print(f"ERROR: Python 3.12+ required, got {sys.version}")
        sys.exit(1)

    # Check dependencies
    try:
        import uvicorn
        import fastapi
        import jinja2
    except ImportError as e:
        print(f"ERROR: Missing dependency: {e}")
        print("Run: pip install -r requirements.txt")
        sys.exit(1)

    # Print banner
    print()
    print("  ╔══════════════════════════════════════════╗")
    print("  ║   PROJECT SYNDICATE — Mission Control    ║")
    print("  ║   http://localhost:8000                  ║")
    print("  ╚══════════════════════════════════════════╝")
    print()

    # Start server
    uvicorn.run(
        "src.web.app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )


if __name__ == "__main__":
    main()
