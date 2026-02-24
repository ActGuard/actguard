# ruff: noqa: E402
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for candidate in (str(REPO_ROOT / "libs" / "sdk-py"), str(REPO_ROOT / "examples" / "00_shared")):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from actguard import BudgetGuard, RunContext, max_attempts
from actguard.exceptions import (
    ActGuardError,
    CircuitOpenError,
    MaxAttemptsExceeded,
    RateLimitExceeded,
)

from modes import Mode, notify_attempts, parse_mode, should_duplicate_incident
from tools import create_incident, get_ticket_text, load_env_if_present, lookup_status, notify_oncall, summarize_ticket


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ActGuard demo with Google ADK")
    parser.add_argument("--user_id", default="alice")
    parser.add_argument("--ticket_id", default="T-1001")
    parser.add_argument("--ticket_text")
    parser.add_argument("--mode", default="happy", choices=[m.value for m in Mode])
    parser.add_argument("--run_id")
    parser.add_argument("--token_limit", type=int)
    parser.add_argument("--usd_limit", type=float)
    parser.add_argument("--no_llm", action="store_true")
    return parser.parse_args()


def main() -> int:
    load_env_if_present()
    args = parse_args()
    mode = parse_mode(args.mode)
    ticket_id, ticket_text = get_ticket_text(args.ticket_id, args.ticket_text)
    guards: list[str] = []

    @max_attempts(calls=2)
    def notify_with_max_attempts(user_id: str, channel: str, message: str) -> None:
        notify_oncall(user_id, channel, message)

    with RunContext(run_id=args.run_id) as run:
        with BudgetGuard(
            user_id=args.user_id,
            token_limit=args.token_limit,
            usd_limit=args.usd_limit,
        ) as budget:
            if args.no_llm:
                summary = summarize_ticket(args.user_id, ticket_text, no_llm=True)
            else:
                try:
                    __import__("google.adk")
                    summary = summarize_ticket(args.user_id, ticket_text, no_llm=False)
                except Exception:
                    summary = summarize_ticket(args.user_id, ticket_text, no_llm=True)

            service = str(summary.get("service") or "payments")
            urgent = bool(summary.get("urgent", False))
            severity = str(summary.get("severity") or "high")
            status = "unknown"
            incident_id = None
            notified = False

            if mode is Mode.DEPENDENCY_DOWN:
                for _ in range(3):
                    try:
                        lookup_status(args.user_id, service, mode=mode.value)
                    except CircuitOpenError as exc:
                        guards.append(f"{exc.__class__.__name__}: {exc}")
                        status = "down"
                        break
                    except Exception as exc:  # noqa: BLE001
                        guards.append(f"{exc.__class__.__name__}: {exc}")
                if status == "unknown":
                    status = "down"
            else:
                try:
                    status = lookup_status(args.user_id, service, mode=mode.value)
                except Exception as exc:  # noqa: BLE001
                    guards.append(f"{exc.__class__.__name__}: {exc}")

            if urgent and status in {"degraded", "down"}:
                key = f"inc-{ticket_id}"
                incident_id = create_incident(
                    args.user_id,
                    f"{ticket_id}: {ticket_text[:80]}",
                    severity,
                    idempotency_key=key,
                )
                if should_duplicate_incident(mode):
                    create_incident(
                        args.user_id,
                        f"{ticket_id}: {ticket_text[:80]}",
                        severity,
                        idempotency_key=key,
                    )

            if urgent:
                for _ in range(notify_attempts(mode)):
                    try:
                        notify_with_max_attempts(
                            args.user_id,
                            "pagerduty",
                            f"Urgent {ticket_id} status={status}",
                        )
                        notified = True
                    except (RateLimitExceeded, MaxAttemptsExceeded, ActGuardError) as exc:
                        guards.append(f"{exc.__class__.__name__}: {exc}")

            result = {
                "ticket_id": ticket_id,
                "urgent": urgent,
                "service": service,
                "status": status,
                "incident_id": incident_id,
                "notified": notified,
            }

            print(f"Framework: google_adk | run_id={run.run_id}")
            print(f"Result: {result}")
            print("Guards:")
            if guards:
                for item in guards:
                    print(f"- {item}")
            else:
                print("- none")
            print(f"Budget: tokens_used={budget.tokens_used} usd_used={budget.usd_used:.6f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
