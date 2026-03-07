# ruff: noqa: E402
from __future__ import annotations

import argparse
import sys
from contextlib import nullcontext
from pathlib import Path
from typing import Any

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

import logging

logging.getLogger("google.genai.types").setLevel(logging.ERROR)

from modes import Mode, notify_attempts, parse_mode, should_duplicate_incident
from tools import create_incident, get_ticket_text, load_env_if_present, lookup_status, notify_oncall, summarize_ticket


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ActGuard demo with Google ADK")
    parser.add_argument("--user_id", default="alice")
    parser.add_argument("--ticket_id", default="T-1001")
    parser.add_argument("--ticket_text")
    parser.add_argument("--mode", default="happy", choices=[m.value for m in Mode])
    parser.add_argument("--run_id")
    parser.add_argument("--usd_limit", type=float)
    parser.add_argument("--no_llm", action="store_true")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Node functions — same guard-exercising logic as the LangGraph example.
# Each mutates the shared state dict in place and returns a status string.
# ---------------------------------------------------------------------------

def summarize_node(state: dict, no_llm: bool) -> str:
    state["summary"] = summarize_ticket(
        state["user_id"], state["ticket_text"], no_llm=no_llm
    )
    state["service"] = str(state["summary"].get("service") or "payments")
    state["urgent"] = bool(state["summary"].get("urgent", False))
    state["severity"] = str(state["summary"].get("severity") or "high")
    return f"summarized: service={state['service']} urgent={state['urgent']}"


def status_node(state: dict) -> str:
    state["status"] = "unknown"
    if state["mode"] is Mode.DEPENDENCY_DOWN:
        for _ in range(3):
            try:
                lookup_status(
                    state["user_id"], state["service"], mode=state["mode"].value
                )
            except CircuitOpenError as exc:
                state["guards"].append(f"{exc.__class__.__name__}: {exc}")
                state["status"] = "down"
                break
            except Exception as exc:  # noqa: BLE001
                state["guards"].append(f"{exc.__class__.__name__}: {exc}")
        if state["status"] == "unknown":
            state["status"] = "down"
        return f"status={state['status']}"

    try:
        state["status"] = lookup_status(
            state["user_id"], state["service"], mode=state["mode"].value
        )
    except Exception as exc:  # noqa: BLE001
        state["guards"].append(f"{exc.__class__.__name__}: {exc}")
    return f"status={state['status']}"


def decision_node(state: dict) -> str:
    state["should_create_incident"] = bool(
        state["urgent"] and state["status"] in {"degraded", "down"}
    )
    return f"should_create_incident={state['should_create_incident']}"


def incident_node(state: dict) -> str:
    if not state["should_create_incident"]:
        return "incident=skipped"

    key = f"inc-{state['ticket_id']}"
    state["incident_id"] = create_incident(
        state["user_id"],
        f"{state['ticket_id']}: {state['ticket_text'][:80]}",
        state["severity"],
        idempotency_key=key,
    )
    if should_duplicate_incident(state["mode"]):
        create_incident(
            state["user_id"],
            f"{state['ticket_id']}: {state['ticket_text'][:80]}",
            state["severity"],
            idempotency_key=key,
        )
    return f"incident_id={state['incident_id']}"


def notify_node(state: dict, notify_fn: Any) -> str:
    if not state["urgent"]:
        return "notify=skipped"

    for _ in range(notify_attempts(state["mode"])):
        try:
            notify_fn(
                state["user_id"],
                "pagerduty",
                f"Urgent {state['ticket_id']} status={state['status']}",
            )
            state["notified"] = True
        except (RateLimitExceeded, MaxAttemptsExceeded, ActGuardError) as exc:
            state["guards"].append(f"{exc.__class__.__name__}: {exc}")
    return f"notified={state['notified']}"


# ---------------------------------------------------------------------------
# ADK pipeline builder
# ---------------------------------------------------------------------------

def build_pipeline(
    args: argparse.Namespace,
    mode: Mode,
    ticket_id: str,
    ticket_text: str,
    notify_fn: Any,
) -> tuple[Any, dict]:
    import os

    from google.adk.agents.llm_agent import LlmAgent
    from google.adk.agents.sequential_agent import SequentialAgent

    model = os.getenv("ACTGUARD_DEMO_MODEL", "gemini-2.5-flash")

    state: dict = {
        "user_id": args.user_id,
        "ticket_id": ticket_id,
        "ticket_text": ticket_text,
        "mode": mode,
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

    def summarize_tool() -> str:
        return summarize_node(state, no_llm=args.no_llm)

    def status_tool() -> str:
        return status_node(state)

    def decision_tool() -> str:
        return decision_node(state)

    def incident_tool() -> str:
        return incident_node(state)

    def notify_tool() -> str:
        return notify_node(state, notify_fn)

    summarize_agent = LlmAgent(
        name="SummarizeAgent",
        model=model,
        instruction="Call summarize_tool and report the result.",
        tools=[summarize_tool],
        output_key="summarize_done",
    )
    status_agent = LlmAgent(
        name="StatusAgent",
        model=model,
        instruction="Call status_tool and report the result.",
        tools=[status_tool],
        output_key="status_done",
    )
    decision_agent = LlmAgent(
        name="DecisionAgent",
        model=model,
        instruction="Call decision_tool and report the result.",
        tools=[decision_tool],
        output_key="decision_done",
    )
    incident_agent = LlmAgent(
        name="IncidentAgent",
        model=model,
        instruction="Call incident_tool and report the result.",
        tools=[incident_tool],
        output_key="incident_done",
    )
    notify_agent = LlmAgent(
        name="NotifyAgent",
        model=model,
        instruction="Call notify_tool and report the result.",
        tools=[notify_tool],
        output_key="notify_done",
    )

    pipeline = SequentialAgent(
        name="SupportTriageSequentialAgent",
        sub_agents=[
            summarize_agent, status_agent, decision_agent, incident_agent, notify_agent
        ],
    )
    return pipeline, state


# ---------------------------------------------------------------------------
# ADK invocation helper
# ---------------------------------------------------------------------------

def _invoke_adk_agent(agent: Any, prompt: str) -> None:
    import asyncio

    from google.adk.runners import InMemoryRunner

    runner = InMemoryRunner(agent=agent)
    asyncio.run(runner.run_debug(prompt, quiet=True))


# ---------------------------------------------------------------------------
# No-LLM fallback: run all five nodes directly without the ADK pipeline
# ---------------------------------------------------------------------------

def _run_nodes_directly(
    args: argparse.Namespace,
    mode: Mode,
    ticket_id: str,
    ticket_text: str,
    notify_fn: Any,
) -> dict:
    state: dict = {
        "user_id": args.user_id,
        "ticket_id": ticket_id,
        "ticket_text": ticket_text,
        "mode": mode,
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
    summarize_node(state, no_llm=True)
    status_node(state)
    decision_node(state)
    incident_node(state)
    notify_node(state, notify_fn)
    return state


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

_GOOGLE_ENV_KEYS = (
    "GOOGLE_API_KEY",
    "GOOGLE_GENAI_USE_VERTEXAI",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
)


def _load_google_env() -> None:
    """Load Google credentials from .env (load_env_if_present skips these keys)."""
    import os

    for candidate in (Path(__file__).parent / ".env", REPO_ROOT / ".env"):
        if not candidate.is_file():
            continue
        for raw in candidate.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key in _GOOGLE_ENV_KEYS and key not in os.environ and val:
                os.environ[key] = val
        break


def main() -> int:
    load_env_if_present()
    _load_google_env()
    args = parse_args()
    mode = parse_mode(args.mode)
    ticket_id, ticket_text = get_ticket_text(args.ticket_id, args.ticket_text)

    @max_attempts(calls=2)
    def notify_with_max_attempts(user_id: str, channel: str, message: str) -> None:
        notify_oncall(user_id, channel, message)

    client = actguard.Client.from_env()
    try:
        with client.run(user_id=args.user_id, run_id=args.run_id) as run:
            budget_scope = (
                client.budget_guard(usd_limit=args.usd_limit)
                if args.usd_limit is not None
                else nullcontext(None)
            )
            with budget_scope as budget:
                if args.no_llm:
                    # Run nodes directly — no LLM credentials needed.
                    state = _run_nodes_directly(
                        args, mode, ticket_id, ticket_text, notify_with_max_attempts
                    )
                else:
                    pipeline, state = build_pipeline(
                        args, mode, ticket_id, ticket_text, notify_with_max_attempts
                    )
                    _invoke_adk_agent(pipeline, f"Support ticket:\n{ticket_text}")

            result = {
                "ticket_id": state["ticket_id"],
                "urgent": state["urgent"],
                "service": state["service"],
                "status": state["status"],
                "incident_id": state["incident_id"],
                "notified": state["notified"],
            }

            print(f"Framework: google_adk | run_id={run.run_id}")
            print(f"Result: {result}")
            print("Guards:")
            if state["guards"]:
                for item in state["guards"]:
                    print(f"- {item}")
            else:
                print("- none")
            if budget is not None:
                print(f"Budget: tokens_used={budget.tokens_used} usd_used={budget.usd_used:.6f}")
            else:
                print("Budget: skipped (set --usd_limit and ACTGUARD_CONFIG to enable)")
    finally:
        client.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
