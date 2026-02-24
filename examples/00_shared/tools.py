from __future__ import annotations

import os
import re
import time
from hashlib import sha1
from pathlib import Path

from actguard import circuit_breaker, idempotent, rate_limit, timeout

from modes import Mode

LOOKUP_TIMEOUT_S = 0.2
LOOKUP_SLOW_SLEEP_S = 0.5

TICKETS: dict[str, str] = {
    "T-1001": "Payments API returns 502 for checkout. Multiple customers blocked.",
    "T-1002": "Dashboard is slower than normal but users can complete actions.",
}


def load_env_if_present() -> None:
    """Load OPENAI_API_KEY / ACTGUARD_DEMO_MODEL from .env if available.

    We only set values that are not already present in the environment.
    """
    candidates = [
        os.path.join(os.getcwd(), ".env"),
        os.path.join(Path(__file__).resolve().parents[2], ".env"),
    ]
    for path in candidates:
        if not os.path.exists(path):
            continue
        _load_simple_dotenv(path)
        break


@timeout(LOOKUP_TIMEOUT_S)
@circuit_breaker(name="service_status", max_fails=2, reset_timeout=30.0)
def lookup_status(user_id: str, service: str, *, mode: str) -> str:
    _ = user_id
    if mode == Mode.SLOW_DEPENDENCY.value:
        time.sleep(LOOKUP_SLOW_SLEEP_S)
        return "ok"
    if mode == Mode.DEPENDENCY_DOWN.value:
        raise ConnectionError(f"{service} dependency unavailable")
    return "degraded" if service == "payments" else "ok"


@idempotent(ttl_s=600, on_duplicate="return")
def create_incident(
    user_id: str,
    title: str,
    severity: str,
    *,
    idempotency_key: str,
) -> str:
    digest = sha1(f"{user_id}:{title}:{severity}".encode("utf-8")).hexdigest()[:10]
    return f"inc_{digest}"


@rate_limit(max_calls=1, period=60, scope="user_id")
def notify_oncall(user_id: str, channel: str, message: str) -> None:
    _ = (user_id, channel, message)


def get_ticket_text(ticket_id: str, ticket_text: str | None) -> tuple[str, str]:
    if ticket_text:
        return ticket_id or "custom", ticket_text
    return ticket_id, TICKETS.get(ticket_id, TICKETS["T-1001"])


def summarize_ticket(user_id: str, ticket_text: str, *, no_llm: bool) -> dict:
    _ = user_id
    if no_llm:
        return _stub_summary(ticket_text)

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return _stub_summary(ticket_text)

    try:
        from openai import OpenAI
    except Exception:
        return _stub_summary(ticket_text)

    client = OpenAI(api_key=api_key)
    model = os.getenv("ACTGUARD_DEMO_MODEL", "gpt-4o-mini")
    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "Return JSON with fields summary, urgent, severity, service "
                    "for this support ticket."
                ),
            },
            {"role": "user", "content": ticket_text},
        ],
        temperature=0,
    )
    raw = response.choices[0].message.content or ""
    return _parse_summary(raw, ticket_text)


def _stub_summary(ticket_text: str) -> dict:
    lowered = ticket_text.lower()
    urgent = any(word in lowered for word in ("outage", "502", "down", "blocked"))
    return {
        "summary": ticket_text[:140],
        "urgent": urgent,
        "severity": "high" if urgent else "low",
        "service": "payments" if "payment" in lowered or "checkout" in lowered else "unknown",
    }


def _parse_summary(raw: str, fallback: str) -> dict:
    urgent = bool(re.search(r'"urgent"\s*:\s*true', raw.lower()))
    severity = re.search(r'"severity"\s*:\s*"([^"]+)"', raw.lower())
    service = re.search(r'"service"\s*:\s*"([^"]+)"', raw.lower())
    return {
        "summary": raw.strip() or fallback[:140],
        "urgent": urgent,
        "severity": severity.group(1) if severity else ("high" if urgent else "low"),
        "service": service.group(1) if service else "unknown",
    }


def _load_simple_dotenv(path: str) -> None:
    wanted = {"OPENAI_API_KEY", "ACTGUARD_DEMO_MODEL"}
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key not in wanted or os.getenv(key):
                continue
            value = value.strip().strip("'").strip('"')
            os.environ[key] = value
