"""
Vote weight lookup by prestige title.

Prestige is earned through sustained performance over time.
Higher prestige = more governance influence.
"""

__version__ = "0.1.0"

PRESTIGE_VOTE_WEIGHTS = {
    "unproven": 0.5,
    "apprentice": 0.5,
    "journeyman": 1.0,
    "proven": 1.0,
    "expert": 1.5,
    "veteran": 1.5,
    "master": 2.0,
    "elite": 2.0,
    "grandmaster": 3.0,
    "legendary": 3.0,
}


def get_vote_weight(prestige_title: str | None) -> float:
    """Return the vote weight for a given prestige title.

    Defaults to 0.5 (unproven) if title is None, empty,
    or not recognized.
    """
    if not prestige_title:
        return 0.5
    return PRESTIGE_VOTE_WEIGHTS.get(prestige_title.lower(), 0.5)
