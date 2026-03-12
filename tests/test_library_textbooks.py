"""
Tests for The Library — Textbooks (Static Knowledge)

Verifies textbook listing, retrieval, search, and placeholder detection.
"""

__version__ = "0.4.0"

import pytest

from src.library.library_service import LibraryService


@pytest.fixture
def library():
    """LibraryService with no DB or Agora (textbook methods are file I/O only)."""
    return LibraryService(db_session_factory=None, agora_service=None)


def test_list_textbooks(library):
    """All 8 placeholder textbooks listed."""
    books = library.list_textbooks()
    assert len(books) == 8
    titles = [b["title"] for b in books]
    assert "Market Mechanics" in titles
    assert "Thinking Efficiently" in titles


def test_list_textbooks_status(library):
    """All placeholders have status='placeholder'."""
    books = library.list_textbooks()
    for book in books:
        assert book["status"] == "placeholder"


def test_get_textbook_by_topic(library):
    """'market' returns 01_market_mechanics.md content."""
    content = library.get_textbook("market")
    assert content is not None
    assert "Market Mechanics" in content


def test_get_textbook_fuzzy_match(library):
    """'risk' returns 03_risk_management.md."""
    content = library.get_textbook("risk")
    assert content is not None
    assert "Risk Management" in content


def test_get_textbook_not_found(library):
    """Unknown topic returns None."""
    content = library.get_textbook("quantum_physics")
    assert content is None


def test_search_textbooks(library):
    """'order' finds results in market_mechanics."""
    results = library.search_textbooks("order")
    assert len(results) > 0
    filenames = [r["filename"] for r in results]
    assert "01_market_mechanics.md" in filenames


def test_search_textbooks_no_results(library):
    """Nonexistent keyword returns empty."""
    results = library.search_textbooks("xyzzy_nonexistent_12345")
    assert results == []


def test_is_textbook_available(library):
    """Placeholders return False."""
    assert library.is_textbook_available("01_market_mechanics.md") is False


def test_is_textbook_available_missing_file(library):
    """Missing file returns False."""
    assert library.is_textbook_available("99_nonexistent.md") is False
