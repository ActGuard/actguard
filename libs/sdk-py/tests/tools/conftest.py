import pytest

import actguard.tools._cache as _cache_mod
import actguard._config as _config_mod
from actguard.tools._cache import InMemoryCache


@pytest.fixture(autouse=True)
def fresh_cache():
    _cache_mod._cache_instance = InMemoryCache()
    yield
    _cache_mod._cache_instance = None


@pytest.fixture(autouse=True)
def reset_config():
    _config_mod._config = None
    yield
    _config_mod._config = None
