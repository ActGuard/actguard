"""Tests for clear SSL certificate error messages."""
import ssl
import urllib.error
from unittest.mock import patch

import pytest

from actguard._monitoring import (
    SSL_CERT_FIX_MESSAGE,
    ActGuardMonitoringWarning,
    _failure_kind,
    _is_ssl_cert_error,
)
from actguard.exceptions import BudgetTransportError


# ---------------------------------------------------------------------------
# _is_ssl_cert_error helper
# ---------------------------------------------------------------------------


def _make_ssl_cert_error(msg="[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"):
    """Build an ssl.SSLCertVerificationError (verify_code=1)."""
    exc = ssl.SSLCertVerificationError(1, msg)
    return exc


class TestIsSSLCertError:
    def test_direct_ssl_cert_verification_error(self):
        assert _is_ssl_cert_error(_make_ssl_cert_error()) is True

    def test_ssl_error_with_cert_verify_string(self):
        exc = ssl.SSLError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")
        assert _is_ssl_cert_error(exc) is True

    def test_wrapped_in_urlerror(self):
        inner = _make_ssl_cert_error()
        outer = urllib.error.URLError(inner)
        assert _is_ssl_cert_error(outer) is True

    def test_chained_via_cause(self):
        inner = _make_ssl_cert_error()
        outer = Exception("something went wrong")
        outer.__cause__ = inner
        assert _is_ssl_cert_error(outer) is True

    def test_unrelated_error_returns_false(self):
        assert _is_ssl_cert_error(ConnectionError("refused")) is False

    def test_unrelated_ssl_error_returns_false(self):
        exc = ssl.SSLError(1, "[SSL: WRONG_VERSION_NUMBER] wrong version number")
        assert _is_ssl_cert_error(exc) is False


# ---------------------------------------------------------------------------
# _failure_kind returns "ssl_cert"
# ---------------------------------------------------------------------------


class TestFailureKindSSL:
    def test_ssl_cert_error_kind(self):
        exc = _make_ssl_cert_error()
        assert _failure_kind(exc, status_code=None) == "ssl_cert"

    def test_ssl_cert_wrapped_in_urlerror(self):
        inner = _make_ssl_cert_error()
        outer = urllib.error.URLError(inner)
        assert _failure_kind(outer, status_code=None) == "ssl_cert"


# ---------------------------------------------------------------------------
# BudgetTransport.post() — immediate raise, no retries
# ---------------------------------------------------------------------------


class TestBudgetTransportSSL:
    def test_raises_immediately_with_fix_message(self):
        from actguard._config import ActGuardConfig
        from actguard.transport.budget_api import BudgetTransport

        config = ActGuardConfig(
            api_key="test-key",
            gateway_url="https://api.actguard.ai",
        )
        transport = BudgetTransport(config)

        ssl_exc = _make_ssl_cert_error()
        url_error = urllib.error.URLError(ssl_exc)

        with patch("urllib.request.urlopen", side_effect=url_error):
            with pytest.raises(BudgetTransportError, match="SSL certificate verification failed"):
                transport.post(path="/api/v1/budget/reserve", payload={"foo": "bar"})

    def test_no_retries_on_ssl_error(self):
        from actguard._config import ActGuardConfig
        from actguard.transport.budget_api import BudgetTransport

        config = ActGuardConfig(
            api_key="test-key",
            gateway_url="https://api.actguard.ai",
            budget_max_retries=3,
        )
        transport = BudgetTransport(config)

        ssl_exc = _make_ssl_cert_error()
        url_error = urllib.error.URLError(ssl_exc)

        with patch("urllib.request.urlopen", side_effect=url_error) as mock_urlopen:
            with pytest.raises(BudgetTransportError):
                transport.post(path="/api/v1/budget/reserve", payload={})
            assert mock_urlopen.call_count == 1


# ---------------------------------------------------------------------------
# EventClient._ship_with_retry() — immediate warn, no retries
# ---------------------------------------------------------------------------


class TestEventClientSSL:
    def test_warns_immediately_with_fix_message(self):
        from actguard._config import ActGuardConfig
        from actguard.events.client import EventClient

        config = ActGuardConfig(
            api_key="test-key",
            gateway_url="https://api.actguard.ai",
        )

        # Construct without starting the worker thread
        client = object.__new__(EventClient)
        client._config = config
        client._stop = __import__("threading").Event()

        ssl_exc = _make_ssl_cert_error()
        url_error = urllib.error.URLError(ssl_exc)

        with patch("urllib.request.urlopen", side_effect=url_error) as mock_urlopen:
            with pytest.warns(ActGuardMonitoringWarning, match="ssl_cert"):
                client._ship_with_retry([{"event": "test"}])
            assert mock_urlopen.call_count == 1
