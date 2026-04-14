## PROJECT SYNDICATE — CLAUDE CODE KICKOFF PROMPT

## Copy everything below the line and paste it into Claude Code

## ================================================================

I'm starting a new project called Project Syndicate. Read the CLAUDE.md file first to understand the full context.

This is Phase 0 — Foundation. Here's what I need you to build in this session:

**Step 1 — Verify Environment:**

* Confirm Python 3.12+ is available
* Confirm PostgreSQL is running and accessible
* Confirm Redis/Memurai is running (redis-cli ping)
* Confirm git is configured and the remote is set to https://github.com/cryptorhoeck/project-syndicate.git

**Step 2 — Create the full directory structure** as defined in CLAUDE.md. Every directory should have a placeholder **init**.py or README.md so git tracks them.

**Step 3 — Set up the Python virtual environment:**

* Create .venv
* Create requirements.txt with initial dependencies:

  * anthropic (Claude API SDK)
  * langgraph
  * langchain-anthropic
  * langchain-core
  * ccxt (crypto exchange library)
  * web3 (DeFi/on-chain)
  * redis (message bus client)
  * psycopg2-binary (PostgreSQL client)
  * sqlalchemy (ORM)
  * alembic (database migrations)
  * fastapi (API framework)
  * uvicorn (ASGI server)
  * httpx (async HTTP client)
  * python-dotenv (env var management)
  * pydantic (data validation)
  * pydantic-settings (settings management)
  * pytest (testing)
  * pytest-asyncio (async test support)
  * apscheduler (scheduled tasks)
  * structlog (structured logging)
* Install all dependencies

**Step 4 — Create the .env.example** with all required environment variables (with placeholder values):

* ANTHROPIC\_API\_KEY
* DATABASE\_URL (PostgreSQL connection string)
* REDIS\_URL
* EXCHANGE\_API\_KEY / EXCHANGE\_API\_SECRET (Kraken)
* EXCHANGE\_SECONDARY\_API\_KEY / EXCHANGE\_SECONDARY\_API\_SECRET (Binance, optional)
* ALERT\_EMAIL\_TO / ALERT\_EMAIL\_FROM / SMTP settings
* SOLANA\_WALLET\_PRIVATE\_KEY (Phase 5, placeholder)
* TWITTER\_API\_KEY / TWITTER\_API\_SECRET (Phase 5, placeholder)
* CIRCUIT\_BREAKER\_THRESHOLD=0.75
* YELLOW\_ALERT\_THRESHOLD=0.15
* RED\_ALERT\_THRESHOLD=0.30
* DEFAULT\_SURVIVAL\_CLOCK\_DAYS=14
* MAX\_AGENTS=20
* LOG\_LEVEL=INFO

**Step 5 — Create the .gitignore** covering:

* .venv/, **pycache**/, .env, \*.pyc, backups/, data/, .pytest\_cache/, \*.egg-info/

**Step 6 — Create the PostgreSQL schema** using SQLAlchemy models (src/common/models.py) and Alembic migrations:

* agents table (id, name, type, status, parent\_id, generation, capital\_allocated, reputation\_score, prestige\_title, survival\_clock\_start, survival\_clock\_end, thinking\_budget\_daily, thinking\_budget\_used\_today, created\_at, terminated\_at, termination\_reason, strategy\_summary, config\_json)
* transactions table (id, agent\_id, type, exchange, symbol, side, amount, price, fee, pnl, timestamp)
* messages table (id, agent\_id, channel, content, metadata\_json, timestamp) — The Agora
* evaluations table (id, agent\_id, evaluation\_type, pnl\_gross, pnl\_net, api\_cost, sharpe\_ratio, reputation\_change, result \[survive/probation/terminate], notes, timestamp)
* reputation\_transactions table (id, from\_agent\_id, to\_agent\_id, amount, reason, related\_trade\_id, timestamp)
* sips table (id, proposing\_agent\_id, title, description, status \[proposed/debating/consensus/approved/rejected], votes\_for, votes\_against, owner\_decision, created\_at, resolved\_at)
* system\_state table (id, total\_treasury, peak\_treasury, current\_regime, active\_agent\_count, last\_backup\_at, last\_heartbeat\_at, updated\_at)
* lineage table (agent\_id, parent\_id, generation, lineage\_path, strategy\_heritage\_json)

**Step 7 — Create the base Agent class** (src/common/base\_agent.py):

* Abstract base class that all agents inherit from
* Lifecycle methods: initialize(), run(), evaluate(), hibernate(), wake(), terminate()
* Agora integration: post\_to\_agora(channel, content), read\_agora(channel, since)
* Thinking tax tracking: every API call increments a counter and cost
* Status management: INITIALIZING, ACTIVE, HIBERNATING, EVALUATING, TERMINATED
* Logging with structlog
* Async support

**Step 8 — Create the backup system** (scripts/backup.py):

* Timestamped backup of database (pg\_dump)
* Timestamped backup of config files
* Backup rotation (keep last 7 daily, last 4 weekly)
* Can be called before any destructive operation

**Step 9 — Create the Dead Man's Switch** (src/risk/heartbeat.py):

* Standalone script (NOT part of any agent framework)
* Checks every 60 seconds: database accessible? Redis accessible? Warden process alive?
* If any check fails 3 consecutive times: log critical error, send alert, and (when exchange integration exists) kill all API connections
* Runs as its own independent process

**Step 10 — Create initial CHANGELOG.md and CURRENT\_STATUS.md**

**Step 11 — Initial git commit and push**

Before you start, confirm you've read and understood the CLAUDE.md, then proceed through each step in order. Ask me if anything is unclear. Use CMD commands only, never PowerShell.

