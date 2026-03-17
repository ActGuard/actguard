"""actGuard integration for Agno AgentOS apps."""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Callable, Optional

from actguard.exceptions import ActGuardPaymentRequired, BudgetExceededError

logger = logging.getLogger("actguard.integrations.agno")

if TYPE_CHECKING:
    from actguard.client import Client


class ActGuardMiddleware:
    """ASGI middleware that wraps each HTTP request in actGuard run + budget context.

    Usage with AgentOS::

        app = agent_os.get_app()
        app.add_middleware(
            ActGuardMiddleware,
            client=agc,
            usd_limit=0.5,
        )
    """

    def __init__(
        self,
        app,
        *,
        client: "Client",
        usd_limit: Optional[float] = None,
        plan_key: Optional[str] = None,
        user_id_header: str = "X-User-Id",
        default_user_id: str = "anonymous",
        user_id_resolver: Optional[Callable] = None,
        on_budget_exceeded: Optional[Callable] = None,
    ) -> None:
        self.app = app
        self.client = client
        self.usd_limit = usd_limit
        self.plan_key = plan_key
        self.user_id_header = user_id_header
        self.default_user_id = default_user_id
        self.user_id_resolver = user_id_resolver
        self.on_budget_exceeded = on_budget_exceeded

    def _extract_user_id(self, scope) -> str:
        if self.user_id_resolver is not None:
            return self.user_id_resolver(scope)
        headers = dict(scope.get("headers", []))
        header_key = self.user_id_header.lower().encode()
        user_id_bytes = headers.get(header_key)
        if user_id_bytes:
            return user_id_bytes.decode("utf-8", errors="replace")
        return self.default_user_id

    async def _send_402(self, send, exc: Exception) -> None:
        body = json.dumps({
            "error": {
                "code": getattr(exc, "code", "budget.limit_exceeded"),
                "reason": getattr(exc, "reason", "budget_exhausted"),
                "message": str(exc),
            }
        }).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": 402,
            "headers": [
                [b"content-type", b"application/json"],
                [b"content-length", str(len(body)).encode()],
            ],
        })
        await send({
            "type": "http.response.body",
            "body": body,
        })

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)

        user_id = self._extract_user_id(scope)

        try:
            with self.client.run(user_id=user_id):
                with self.client.request_budget_session(
                    usd_limit=self.usd_limit,
                    plan_key=self.plan_key,
                    user_id=user_id,
                ):
                    try:
                        await self.app(scope, receive, send)
                    except (BudgetExceededError, ActGuardPaymentRequired) as exc:
                        if self.on_budget_exceeded is not None:
                            await self.on_budget_exceeded(scope, receive, send, exc)
                        else:
                            await self._send_402(send, exc)
                    return  # success path — skip the fallback below
        except (BudgetExceededError, ActGuardPaymentRequired):
            raise  # budget errors should NOT be silenced
        except Exception:
            logger.warning(
                "actGuard middleware degraded: could not establish run/budget context. "
                "Request will proceed without budget protection.",
                exc_info=True,
            )

        # Fallback: actguard context failed, run inner app unprotected
        await self.app(scope, receive, send)
