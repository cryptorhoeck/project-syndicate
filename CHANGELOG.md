# Changelog

All notable changes to Project Syndicate will be documented in this file.

## [0.1.0] - 2026-03-12

### Added — Phase 0: Foundation
- Project scaffold and full directory structure
- CLAUDE.md with complete project documentation
- PostgreSQL database with 8 tables: agents, transactions, messages (Agora), evaluations, reputation_transactions, sips, system_state, lineage
- Alembic migration system initialized with initial schema
- SQLAlchemy 2.0 ORM models (`src/common/models.py`)
- Abstract base agent class with lifecycle, Agora integration, and thinking tax tracking (`src/common/base_agent.py`)
- Backup system with pg_dump and config backup, rotation policy (`scripts/backup.py`)
- Dead Man's Switch heartbeat monitor — independent process monitoring PostgreSQL, Redis, and system state freshness (`src/risk/heartbeat.py`)
- Python virtual environment with 20+ dependencies installed
- Environment configuration template (`.env.example`)
- `.gitignore` for Python/IDE/data exclusions
- Redis/Memurai connectivity confirmed
- PostgreSQL initialized and running
