"""
Haiku digester.

Pulls pending wire_raw_items, asks Haiku for a structured event, validates the
JSON, applies severity rules, dedupes, persists the resulting wire_event,
and bills the cost to the Wire treasury ledger.

Behaviour on Haiku output failure (parse error, schema violation):
  - Attempt 1 fails  -> retry once with explicit format reminder
  - Attempt 2 fails  -> mark raw_item dead_letter, never silently digested
                        (Library reflection bug callback)

The digester is sync. It accepts an injected `client` so tests can supply a
fake without touching anthropic.Anthropic.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.wire.constants import (
    AGORA_EVENT_HAIKU_SEVERITY_CAPPED,
    DIGEST_MAX_PARSE_RETRIES,
    DIGEST_SUMMARY_MAX_CHARS,
    DIGESTION_STATUS_DEAD_LETTER,
    DIGESTION_STATUS_DIGESTED,
    DIGESTION_STATUS_PENDING,
    DIRECTIONS_SET,
    EVENT_TYPES_SET,
    SEVERITY_CRITICAL,
    SEVERITY_TRIVIAL,
)
from src.wire.digest.deduper import canonical_hash, find_duplicate
from src.wire.digest.prompts import SYSTEM_PROMPT, build_user_prompt
from src.wire.digest.severity import apply_severity_rules
from src.wire.integration.genesis_regime import maybe_dispatch as dispatch_severity_5
from src.wire.integration.operator_halt import (
    OperatorHaltPublishError,
    publish_halt_for_event,
)
from src.wire.models import WireEvent, WireRawItem, WireTreasuryLedger
from src.wire.publishing.ticker import WireTicker

logger = logging.getLogger(__name__)


class HaikuOutputError(Exception):
    """Raised when Haiku output cannot be parsed or fails schema validation."""


@dataclass(slots=True)
class DigestionResult:
    """Outcome of digesting a single raw item."""

    raw_item_id: int
    status: str  # 'digested' | 'dead_letter'
    event_id: Optional[int] = None
    duplicate_of: Optional[int] = None
    severity: Optional[int] = None
    severity_capped: bool = False
    cost_usd: float = 0.0
    error: Optional[str] = None


@dataclass(slots=True)
class HaikuCallResult:
    """What an injected Haiku client returns. Tests fabricate this directly."""

    text: str
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0


class HaikuClientProto(Protocol):
    def __call__(self, system_prompt: str, user_prompt: str) -> HaikuCallResult: ...


# Default model + pricing (kept close to existing ClaudeClient pricing).
DEFAULT_HAIKU_MODEL = "claude-haiku-4-5-20251001"
_HAIKU_INPUT_PER_MILLION = 1.00
_HAIKU_OUTPUT_PER_MILLION = 5.00


def _calculate_haiku_cost(input_tokens: int, output_tokens: int) -> float:
    return (input_tokens / 1_000_000) * _HAIKU_INPUT_PER_MILLION + (
        output_tokens / 1_000_000
    ) * _HAIKU_OUTPUT_PER_MILLION


def make_default_haiku_client(api_key: Optional[str] = None) -> HaikuClientProto:
    """Build a real anthropic-backed Haiku call function.

    Imported lazily so test paths that supply their own fake client don't need
    a working API key in the environment.
    """

    import anthropic  # noqa: WPS433

    client = anthropic.Anthropic(api_key=api_key)

    def _call(system_prompt: str, user_prompt: str) -> HaikuCallResult:
        response = client.messages.create(
            model=DEFAULT_HAIKU_MODEL,
            max_tokens=512,
            temperature=0.0,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = response.content[0].text if response.content else ""
        input_tokens = getattr(response.usage, "input_tokens", 0) or 0
        output_tokens = getattr(response.usage, "output_tokens", 0) or 0
        return HaikuCallResult(
            text=text,
            cost_usd=_calculate_haiku_cost(input_tokens, output_tokens),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    return _call


# ---------------------------------------------------------------------------
# JSON parsing + schema validation
# ---------------------------------------------------------------------------


def _strip_code_fences(text: str) -> str:
    """Tolerate Haiku occasionally wrapping JSON in ```json ... ``` despite the prompt."""
    s = text.strip()
    if s.startswith("```"):
        # remove leading fence
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        if s.endswith("```"):
            s = s[:-3]
    return s.strip()


def _parse_haiku_output(raw: str) -> dict[str, Any]:
    """Parse Haiku output and validate schema. Raises HaikuOutputError on failure."""
    if not raw:
        raise HaikuOutputError("empty Haiku response")
    try:
        data = json.loads(_strip_code_fences(raw))
    except json.JSONDecodeError as exc:
        raise HaikuOutputError(f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise HaikuOutputError("Haiku output is not a JSON object")

    # Required fields
    for field_name in ("event_type", "severity", "summary"):
        if field_name not in data:
            raise HaikuOutputError(f"missing field: {field_name}")

    event_type = data["event_type"]
    if event_type not in EVENT_TYPES_SET:
        raise HaikuOutputError(f"unknown event_type: {event_type!r}")

    direction = data.get("direction")
    if direction is not None and direction not in DIRECTIONS_SET:
        raise HaikuOutputError(f"invalid direction: {direction!r}")

    summary = data.get("summary") or ""
    if not isinstance(summary, str):
        raise HaikuOutputError("summary must be a string")
    if not summary.strip():
        raise HaikuOutputError("summary is empty")

    # Severity comes from the model — accept any int-like and let the severity
    # adjudicator clamp.
    sev = data["severity"]
    try:
        int(sev)
    except (TypeError, ValueError):
        raise HaikuOutputError(f"non-integer severity: {sev!r}")

    return data


# ---------------------------------------------------------------------------
# HaikuDigester
# ---------------------------------------------------------------------------


@dataclass
class HaikuDigester:
    """Stateful digester. Hold an injected client and a session factory."""

    haiku_client: HaikuClientProto
    session: Session
    on_severity_capped: Optional[Callable[[dict[str, Any]], None]] = None
    now_func: Callable[[], datetime] = field(
        default_factory=lambda: lambda: datetime.now(timezone.utc)
    )
    # Tier 3 post-digest hooks. None = skip that hook.
    ticker: Optional[WireTicker] = None

    def fetch_pending(self, limit: int = 50) -> list[WireRawItem]:
        stmt = (
            select(WireRawItem)
            .where(WireRawItem.digestion_status == DIGESTION_STATUS_PENDING)
            .order_by(WireRawItem.fetched_at.asc())
            .limit(limit)
        )
        return list(self.session.execute(stmt).scalars().all())

    def digest_pending(self, limit: int = 50) -> list[DigestionResult]:
        results: list[DigestionResult] = []
        for raw in self.fetch_pending(limit=limit):
            results.append(self.digest_one(raw))
        return results

    def digest_one(self, raw: WireRawItem) -> DigestionResult:
        """Digest a single raw item. Commits per-item to keep dead-letters and
        successful events independent."""
        item_brief = self._extract_haiku_brief(raw)

        last_error: Optional[str] = None
        parsed: Optional[dict[str, Any]] = None
        total_cost = 0.0

        for attempt in range(DIGEST_MAX_PARSE_RETRIES + 1):
            user_prompt = build_user_prompt(item_brief, repair=(attempt > 0))
            try:
                call = self.haiku_client(SYSTEM_PROMPT, user_prompt)
            except Exception as exc:  # network / API errors
                last_error = f"haiku call failed: {exc}"
                logger.warning("wire.haiku_call_failed", extra={"error": str(exc)})
                break

            total_cost += float(call.cost_usd or 0.0)
            try:
                parsed = _parse_haiku_output(call.text)
                last_error = None
                break
            except HaikuOutputError as exc:
                last_error = str(exc)
                logger.info(
                    "wire.haiku_parse_failed",
                    extra={"attempt": attempt + 1, "error": str(exc)},
                )
                continue

        raw.digestion_attempts = (raw.digestion_attempts or 0) + 1

        if parsed is None:
            raw.digestion_status = DIGESTION_STATUS_DEAD_LETTER
            self.session.add(raw)
            self._record_treasury_cost(total_cost, related_event_id=None)
            self.session.commit()
            return DigestionResult(
                raw_item_id=raw.id,
                status=DIGESTION_STATUS_DEAD_LETTER,
                cost_usd=total_cost,
                error=last_error or "unknown digest failure",
            )

        # Severity adjudication.
        det_sev = self._extract_int(raw.raw_payload, "deterministic_severity")
        det_event_type = self._extract_str(raw.raw_payload, "deterministic_event_type")
        det_coin = self._extract_str(raw.raw_payload, "deterministic_coin")
        det_direction = self._extract_str(raw.raw_payload, "deterministic_direction")
        det_is_macro = self._extract_bool(raw.raw_payload, "deterministic_is_macro")

        sev_result = apply_severity_rules(det_sev, parsed["severity"])
        if sev_result.capped and self.on_severity_capped:
            try:
                self.on_severity_capped(
                    {
                        "raw_item_id": raw.id,
                        "haiku_attempted": parsed["severity"],
                        "final_severity": sev_result.severity,
                    }
                )
            except Exception:  # pragma: no cover — alerting must never break ingest
                logger.exception("wire.severity_capped_callback_failed")

        coin = det_coin or parsed.get("coin")
        if isinstance(coin, str):
            coin = coin.strip() or None
        else:
            coin = None
        is_macro = det_is_macro if det_is_macro is not None else bool(parsed.get("is_macro", False))
        direction = det_direction or parsed.get("direction")
        event_type = det_event_type or parsed["event_type"]
        summary = (parsed.get("summary") or "").strip()
        if len(summary) > DIGEST_SUMMARY_MAX_CHARS:
            summary = summary[: DIGEST_SUMMARY_MAX_CHARS - 1].rstrip() + "…"

        canonical = canonical_hash(coin, event_type, summary)

        # Resolve occurred_at: explicit raw.occurred_at > now()
        occurred_at = raw.occurred_at or self.now_func()
        # Dedup window is centered on this event's own occurrence time so that
        # late digestion of an old item still finds the matching canonical row
        # if one exists within DEDUP_WINDOW_HOURS of the upstream event.
        existing = find_duplicate(self.session, canonical, now=occurred_at)

        # Mark severity-5 events as 'pending' for Genesis regime-review
        # consumption (subsystem H, Option C). Genesis.run_cycle() picks
        # these up at top-of-cycle, then marks 'reviewed' at end-of-cycle.
        # Duplicate severity-5 events do NOT requeue (Genesis already
        # reviewed the original); they remain 'skipped'. Non-sev-5
        # events default to 'skipped' via server_default.
        is_sev5_for_review = (
            sev_result.severity >= SEVERITY_CRITICAL and existing is None
        )
        regime_review_status = "pending" if is_sev5_for_review else "skipped"

        # Persist event.
        event = WireEvent(
            raw_item_id=raw.id,
            canonical_hash=canonical,
            coin=coin,
            is_macro=is_macro,
            event_type=event_type,
            severity=sev_result.severity,
            direction=direction,
            summary=summary or "(no summary)",
            source_url=self._extract_str(raw.raw_payload, "source_url"),
            occurred_at=occurred_at,
            haiku_cost_usd=total_cost,
            duplicate_of=existing.id if existing is not None else None,
            regime_review_status=regime_review_status,
        )
        self.session.add(event)

        raw.digestion_status = DIGESTION_STATUS_DIGESTED
        self.session.add(raw)
        self.session.flush()  # ensure event.id assigned for ledger row
        self._record_treasury_cost(total_cost, related_event_id=event.id)

        # Tier 3 post-digest hooks (ticker push, severity-5 dispatch, op halt).
        if self.ticker is not None:
            self.ticker.publish_event(self.session, event)

        if event.severity >= 5:
            dispatch_severity_5(
                event_id=event.id,
                severity=event.severity,
                coin=event.coin,
                event_type=event.event_type,
                summary=event.summary,
                occurred_at_iso=event.occurred_at.isoformat() if event.occurred_at else None,
            )
            try:
                publish_halt_for_event(
                    event_id=event.id,
                    coin=event.coin,
                    event_type=event.event_type,
                    severity=event.severity,
                    summary=event.summary,
                )
            except OperatorHaltPublishError as exc:
                # Redis-write failure already logged CRITICAL + posted to
                # system-alerts inside publish_halt_for_event. Don't
                # dead-letter the raw item over a halt-store blip — the
                # event itself was digested successfully and the failure
                # has been broadcast cross-process.
                logger.critical(
                    "wire.digest.operator_halt_publish_failed",
                    extra={
                        "raw_item_id": raw.id, "event_id": event.id,
                        "trigger_event_id": exc.trigger_event_id,
                        "coin": exc.coin, "exchange": exc.exchange,
                        "event_type": exc.event_type,
                        "underlying": str(exc.underlying),
                    },
                )

        self.session.commit()

        return DigestionResult(
            raw_item_id=raw.id,
            status=DIGESTION_STATUS_DIGESTED,
            event_id=event.id,
            duplicate_of=existing.id if existing is not None else None,
            severity=sev_result.severity,
            severity_capped=sev_result.capped,
            cost_usd=total_cost,
        )

    # ------- helpers -------

    def _record_treasury_cost(
        self,
        cost_usd: float,
        related_event_id: Optional[int],
    ) -> None:
        if cost_usd <= 0:
            return
        ledger = WireTreasuryLedger(
            cost_category="haiku_digestion",
            cost_usd=cost_usd,
            related_event_id=related_event_id,
        )
        self.session.add(ledger)

    @staticmethod
    def _extract_haiku_brief(raw: WireRawItem) -> str:
        payload = raw.raw_payload or {}
        brief = payload.get("haiku_brief")
        if isinstance(brief, str) and brief.strip():
            return brief
        # fall back to a condensed JSON dump
        try:
            return json.dumps(payload, ensure_ascii=False, default=str)[:2000]
        except (TypeError, ValueError):
            return repr(payload)[:2000]

    @staticmethod
    def _extract_str(payload: dict[str, Any] | None, key: str) -> Optional[str]:
        if not payload:
            return None
        v = payload.get(key)
        if isinstance(v, str) and v.strip():
            return v
        return None

    @staticmethod
    def _extract_int(payload: dict[str, Any] | None, key: str) -> Optional[int]:
        if not payload:
            return None
        v = payload.get(key)
        if v is None:
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _extract_bool(payload: dict[str, Any] | None, key: str) -> Optional[bool]:
        if not payload:
            return None
        v = payload.get(key)
        if v is None:
            return None
        return bool(v)
