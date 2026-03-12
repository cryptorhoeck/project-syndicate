"""
Project Syndicate — Library API Fragment Routes

Returns HTML fragments for Library HTMX interactions.
"""

__version__ = "0.6.0"

import os
import re
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from sqlalchemy import select

from src.common.models import LibraryEntry

router = APIRouter()

TEXTBOOK_DIR = os.path.join("data", "library", "textbooks")


def _get_textbooks() -> list[dict]:
    """Parse textbook files to build entry list."""
    textbook_dir = Path(TEXTBOOK_DIR)
    if not textbook_dir.exists():
        return []

    entries = []
    for filepath in sorted(textbook_dir.glob("*.md")):
        content = filepath.read_text(encoding="utf-8")
        title = ""
        summary = ""
        is_placeholder = "Status:** PLACEHOLDER" in content

        for line in content.splitlines():
            if line.startswith("# ") and not title:
                title = line[2:].strip()
            elif line.strip() and title and not summary:
                if line.startswith(">") or line.startswith("##") or line.startswith("---"):
                    continue
                if "Description" in line:
                    continue
                summary = line.strip()

        desc_match = re.search(
            r"## Description\s*\n\s*\n(.+?)(?:\n\s*\n|\n##)", content, re.DOTALL
        )
        if desc_match:
            summary = desc_match.group(1).strip()

        entries.append({
            "id": 0,
            "title": title or filepath.stem,
            "category": "textbook",
            "summary": summary[:200],
            "source_agent_name": None,
            "view_count": 0,
            "is_placeholder": is_placeholder,
        })

    return entries


@router.get("/entries", response_class=HTMLResponse)
async def library_entries(
    request: Request,
    category: str = "textbook",
    search: str = "",
):
    templates = request.app.state.templates
    factory = request.app.state.db_session_factory

    if category == "textbook":
        entries = _get_textbooks()
        if search:
            q = search.lower()
            entries = [e for e in entries if q in e["title"].lower() or q in (e["summary"] or "").lower()]
    else:
        with factory() as session:
            stmt = select(LibraryEntry).where(LibraryEntry.category == category)
            if search:
                stmt = stmt.where(
                    LibraryEntry.title.ilike(f"%{search}%")
                    | LibraryEntry.content.ilike(f"%{search}%")
                )
            stmt = stmt.order_by(LibraryEntry.created_at.desc()).limit(50)
            rows = list(session.execute(stmt).scalars().all())

            entries = [
                {
                    "id": e.id,
                    "title": e.title,
                    "category": e.category,
                    "summary": (e.summary or "")[:200],
                    "source_agent_name": e.source_agent_name,
                    "view_count": e.view_count or 0,
                    "is_placeholder": False,
                }
                for e in rows
            ]

    return templates.TemplateResponse(
        "fragments/library_entries.html",
        {"request": request, "entries": entries, "category": category},
    )
