from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Evidence:
    kind: str = ""
    system: str = ""
    locator: str = ""
    url: str = ""
    digest_algo: str = ""
    digest: str = ""
    attrs: Dict[str, str] = field(default_factory=dict)
    inline: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "system": self.system,
            "locator": self.locator,
            "url": self.url,
            "digestAlgo": self.digest_algo,
            "digest": self.digest,
            "attrs": self.attrs,
            "inline": self.inline,
        }


@dataclass
class Envelope:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    ts: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )
    tenant_id: str = ""
    user_id: Optional[str] = None
    run_id: str = ""
    trace_id: str = ""
    span_id: str = ""
    category: str = ""
    name: str = ""
    version: int = 1
    severity: str = ""
    outcome: str = ""
    model: Optional[str] = None
    usd_micros: Optional[int] = None
    input_tokens: Optional[int] = None
    cached_input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    digest: str = ""
    digest_algo: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    evidence: List[Evidence] = field(default_factory=list)
    meta: Dict[str, str] = field(default_factory=dict)
    ingested_at: Optional[datetime.datetime] = None

    def to_dict(self) -> dict:
        ts_str = self.ts.isoformat().replace("+00:00", "Z")
        ingested = self.ingested_at if self.ingested_at is not None else self.ts
        ingested_str = ingested.isoformat().replace("+00:00", "Z")
        payload = {
            "id": self.id,
            "ts": ts_str,
            "tenantID": self.tenant_id,
            "runID": self.run_id,
            "traceID": self.trace_id,
            "spanID": self.span_id,
            "category": self.category,
            "name": self.name,
            "version": self.version,
            "severity": self.severity,
            "outcome": self.outcome,
            "digest": self.digest,
            "digestAlgo": self.digest_algo,
            "payload": self.payload,
            "evidence": [e.to_dict() for e in self.evidence],
            "meta": self.meta,
            "ingestedAt": ingested_str,
        }
        if self.user_id is not None:
            payload["userID"] = self.user_id
        if self.model:
            payload["model"] = self.model
        if self.usd_micros is not None:
            payload["usd_micros"] = self.usd_micros
        if self.input_tokens is not None:
            payload["input_tokens"] = self.input_tokens
        if self.cached_input_tokens is not None:
            payload["cached_input_tokens"] = self.cached_input_tokens
        if self.output_tokens is not None:
            payload["output_tokens"] = self.output_tokens
        return payload


class EvidenceProvider:
    """Protocol for objects that can supply Evidence for an event."""

    def current(self) -> List[Evidence]:
        ...


class ActGuardContextEvidenceProvider:
    """Reads run_id and user_id from active context vars."""

    def current(self) -> List[Evidence]:
        try:
            from actguard.core.run_context import get_run_state

            state = get_run_state()
            if state is None:
                return []
            attrs = {"run_id": state.run_id}
            user_id = getattr(state, "user_id", None)
            if user_id:
                attrs["user_id"] = user_id
            ev = Evidence(
                kind="run_context",
                system="actguard",
                attrs=attrs,
            )
            return [ev]
        except Exception:
            return []
