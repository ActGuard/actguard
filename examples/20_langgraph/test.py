# ruff: noqa: E402
from __future__ import annotations

import argparse
import sys

from pathlib import Path
from typing import TypedDict

REPO_ROOT = Path(__file__).resolve().parents[2]
for candidate in (str(REPO_ROOT / "libs" / "sdk-py"), str(REPO_ROOT / "examples" / "00_shared")):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

import actguard
from actguard import max_attempts
from actguard.exceptions import (
    ActGuardError,
    CircuitOpenError,
    MaxAttemptsExceeded,
    RateLimitExceeded,
)
from langgraph.graph import END, StateGraph

from modes import Mode, notify_attempts, parse_mode, should_duplicate_incident
from tools import create_incident, get_ticket_text, load_env_if_present, lookup_status, notify_oncall, summarize_ticket


class GraphState(TypedDict):
    user_id: str
    ticket_id: str
    ticket_text: str
    mode: Mode
    no_llm: bool
    summary: dict
    service: str
    urgent: bool
    severity: str
    status: str
    should_create_incident: bool
    incident_id: str | None
    notified: bool
    guards: list[str]


@max_attempts(calls=2)
def notify_with_max_attempts(user_id: str, channel: str, message: str) -> None:
    notify_oncall(user_id, channel, message)


def summarize_node(state: GraphState) -> GraphState:
    out = dict(state)
    out["summary"] = summarize_ticket(
        out["user_id"],
        out["ticket_text"],
        no_llm=out["no_llm"],
    )
    out["service"] = str(out["summary"].get("service") or "payments")
    out["urgent"] = bool(out["summary"].get("urgent", False))
    out["severity"] = str(out["summary"].get("severity") or "high")
    return out


def status_node(state: GraphState) -> GraphState:
    out = dict(state)
    out["status"] = "unknown"

    if out["mode"] is Mode.DEPENDENCY_DOWN:
        for _ in range(3):
            try:
                lookup_status(out["user_id"], out["service"], mode=out["mode"].value)
            except CircuitOpenError as exc:
                out["guards"].append(f"{exc.__class__.__name__}: {exc}")
                out["status"] = "down"
                break
            except Exception as exc:  # noqa: BLE001
                out["guards"].append(f"{exc.__class__.__name__}: {exc}")
        if out["status"] == "unknown":
            out["status"] = "down"
        return out

    try:
        out["status"] = lookup_status(
            out["user_id"],
            out["service"],
            mode=out["mode"].value,
        )
    except Exception as exc:  # noqa: BLE001
        out["guards"].append(f"{exc.__class__.__name__}: {exc}")

    return out


def decision_node(state: GraphState) -> GraphState:
    out = dict(state)
    out["should_create_incident"] = bool(out["urgent"] and out["status"] in {"degraded", "down"})
    return out


def incident_node(state: GraphState) -> GraphState:
    out = dict(state)
    if not out["should_create_incident"]:
        return out

    key = f"inc-{out['ticket_id']}"
    out["incident_id"] = create_incident(
        out["user_id"],
        f"{out['ticket_id']}: {out['ticket_text'][:80]}",
        out["severity"],
        idempotency_key=key,
    )
    if should_duplicate_incident(out["mode"]):
        create_incident(
            out["user_id"],
            f"{out['ticket_id']}: {out['ticket_text'][:80]}",
            out["severity"],
            idempotency_key=key,
        )
    return out


def notify_node(state: GraphState) -> GraphState:
    out = dict(state)
    if not out["urgent"]:
        return out

    for _ in range(notify_attempts(out["mode"])):
        try:
            notify_with_max_attempts(
                out["user_id"],
                "pagerduty",
                f"Urgent {out['ticket_id']} status={out['status']}",
            )
            out["notified"] = True
        except (RateLimitExceeded, MaxAttemptsExceeded, ActGuardError) as exc:
            out["guards"].append(f"{exc.__class__.__name__}: {exc}")
    return out


def build_graph():
    graph = StateGraph(GraphState)
    graph.add_node("summarize", summarize_node)
    graph.add_node("status", status_node)
    graph.add_node("decision", decision_node)
    graph.add_node("incident", incident_node)
    graph.add_node("notify", notify_node)
    graph.set_entry_point("summarize")
    graph.add_edge("summarize", "status")
    graph.add_edge("status", "decision")
    graph.add_edge("decision", "incident")
    graph.add_edge("incident", "notify")
    graph.add_edge("notify", END)
    return graph.compile()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ActGuard demo with LangGraph")
    parser.add_argument("--user_id", default="alice")
    parser.add_argument("--ticket_id", default="T-1001")
    parser.add_argument("--ticket_text")
    parser.add_argument("--mode", default="happy", choices=[m.value for m in Mode])
    parser.add_argument("--run_id")

    parser.add_argument("--no_llm", action="store_true")
    return parser.parse_args()


def main() -> int:
    load_env_if_present()
    args = parse_args()
    mode = parse_mode(args.mode)
    ticket_id, ticket_text = get_ticket_text(args.ticket_id, args.ticket_text)
    app = build_graph()

    import os
    os.environ["OPENAI_API_KEY"] = "REMOVED_OPENAI_KEY_1"

    initial_state: GraphState = {
        "user_id": args.user_id,
        "ticket_id": ticket_id,
        "ticket_text": ticket_text,
        "mode": mode,
        "no_llm": False,
        "summary": {},
        "service": "unknown",
        "urgent": False,
        "severity": "low",
        "status": "unknown",
        "should_create_incident": False,
        "incident_id": None,
        "notified": False,
        "guards": [],
    }

    client = actguard.Client(api_key="ag_live_-14cdLuV7Rc5UCBNOoXJFA.-b2Z6iVtzdziRX71tp2MRZDMhBRSdtfuMff7-iUB75w", gateway_url="http://localhost:8085")
    try:
        with client.run(user_id=args.user_id, run_id=args.run_id) as run:
            with client.budget_guard(usd_limit=0.1) as budget:
                final_state = app.invoke(initial_state)

                result = {
                    "ticket_id": final_state["ticket_id"],
                    "urgent": final_state["urgent"],
                    "service": final_state["service"],
                    "status": final_state["status"],
                    "incident_id": final_state["incident_id"],
                    "notified": final_state["notified"],
                }

                print(f"Framework: langgraph | run_id={run.run_id}")
                print(f"Result: {result}")
                print("Guards:")
                if final_state["guards"]:
                    for item in final_state["guards"]:
                        print(f"- {item}")
                else:
                    print("- none")
                print(f"Budget: tokens_used={budget.tokens_used} usd_used={budget.usd_used:.6f}")
    finally:
        client.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
