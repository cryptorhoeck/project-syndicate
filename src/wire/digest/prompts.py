"""
Haiku digestion prompts.

Strict JSON output. Schema-validated downstream. Anything that doesn't parse
or doesn't match the closed event_type / direction enums is retried once,
then dead-lettered.

Severity 5 is reserved for deterministic rules. The prompt explicitly
forbids Haiku from assigning 5; if it does anyway, the digester downgrades
to 4 and logs a wire.haiku_severity_capped event.
"""

from __future__ import annotations

from src.wire.constants import DIRECTIONS, EVENT_TYPES, HAIKU_MAX_SEVERITY


SYSTEM_PROMPT = (
    "You are The Wire — Project Syndicate's intelligence digestion service.\n"
    "Your job: convert one raw market data item into a structured event for "
    "autonomous trading agents.\n\n"
    "Output ONLY valid JSON. No markdown fences, no preamble, no commentary.\n"
)


_EVENT_TYPES_LIST = "|".join(EVENT_TYPES)
_DIRECTIONS_LIST = "|".join(DIRECTIONS)


USER_PROMPT_TEMPLATE = (
    "Output JSON matching this schema:\n"
    "{\n"
    '  "coin": "BTC|ETH|SOL|... or null for macro",\n'
    '  "is_macro": true|false,\n'
    f'  "event_type": "{_EVENT_TYPES_LIST}",\n'
    f'  "severity": 1-{HAIKU_MAX_SEVERITY},\n'
    f'  "direction": "{_DIRECTIONS_LIST}",\n'
    '  "summary": "max 200 chars, factual, no speculation, no emoji"\n'
    "}\n\n"
    "RULES:\n"
    "- Be terse. Agents pay tokens to read this.\n"
    "- If item is unclear, set severity 1 and direction neutral.\n"
    "- Never invent details not in the source.\n"
    f"- Never assign severity {HAIKU_MAX_SEVERITY + 1} — system will downgrade and log a violation.\n\n"
    "RAW ITEM:\n"
    "{item_brief}\n\n"
    "OUTPUT (JSON only, no preamble):"
)


REPAIR_PROMPT_SUFFIX = (
    "\n\nThe previous response did not parse as valid JSON or violated the "
    "schema. Reply ONLY with valid JSON matching the schema above. No prose."
)


def build_user_prompt(item_brief: str, repair: bool = False) -> str:
    """Assemble the user prompt for one raw item.

    Args:
        item_brief: Concise human-readable summary of the source item. Sources
            populate this via FetchedItem.haiku_brief; if missing, the digester
            falls back to a JSON dump of raw_payload.
        repair: True on the second-attempt repair call.
    """
    body = USER_PROMPT_TEMPLATE.replace("{item_brief}", item_brief)
    if repair:
        body += REPAIR_PROMPT_SUFFIX
    return body
