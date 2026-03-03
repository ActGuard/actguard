from __future__ import annotations

from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from actguard._config import ActGuardConfig


class ActGuardPlugin:
    """Protocol for ActGuard plugins."""

    name: str

    def evidence_providers(self) -> list:
        ...

    def hooks(self) -> dict:
        ...


_loaded: List[ActGuardPlugin] = []


def get_plugins() -> list:
    return list(_loaded)


def _load(config: "ActGuardConfig") -> None:
    global _loaded
    _loaded = []
    if config is None or not config.plugins:
        return

    _plugin_map = {
        "otel": ("actguard.plugins.otel", "OtelPlugin"),
        "langsmith": ("actguard.plugins.langsmith", "LangSmithPlugin"),
    }

    for plugin_name in config.plugins:
        if plugin_name not in _plugin_map:
            continue
        module_path, class_name = _plugin_map[plugin_name]
        try:
            import importlib

            mod = importlib.import_module(module_path)
            cls = getattr(mod, class_name)
            plugin_cfg = (config.plugin_config or {}).get(plugin_name, {})
            _loaded.append(cls(**plugin_cfg) if plugin_cfg else cls())
        except Exception:
            pass
