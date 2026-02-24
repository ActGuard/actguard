from typing import Any, Dict

from ._config import get_config


def report_event(event: Dict[str, Any]) -> None:
    """Report a tool event to the ActGuard gateway.

    Currently a stub. Future: async HTTP POST to config.gateway_url.
    Called by each tool decorator after every check (allowed or blocked).
    """
    config = get_config()
    if config is None or config.gateway_url is None:
        return
    # TODO: implement HTTP reporting to ActGuard gateway
    # payload includes agent_id, event type, func, scope, outcome, timestamp
    pass
