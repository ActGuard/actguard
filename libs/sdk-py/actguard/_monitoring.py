from __future__ import annotations

import errno
import socket
import ssl
import urllib.error
import warnings

from actguard.exceptions import MonitoringDegradedError

SSL_CERT_FIX_MESSAGE = (
    "SSL certificate verification failed while connecting to the ActGuard API.\n"
    "This usually means Python cannot find trusted CA certificates.\n\n"
    "To fix this:\n"
    "  • macOS (python.org installer): run 'Install Certificates.command' from\n"
    "    /Applications/Python 3.x/ or run:\n"
    "      pip install --upgrade certifi\n"
    "      /Applications/Python\\ 3.*/Install\\ Certificates.command\n"
    "  • Other systems: pip install --upgrade certifi && export SSL_CERT_FILE=$(python3 -m certifi)\n"
    "  • Or set the SSL_CERT_FILE environment variable to a valid CA bundle path."
)


class ActGuardMonitoringWarning(RuntimeWarning):
    """Warning emitted when ActGuard monitoring/reporting degrades open."""

    def __init__(self, error: MonitoringDegradedError) -> None:
        self.error = error
        super().__init__(str(error))


def monitoring_error_from_exception(
    *,
    subsystem: str,
    operation: str,
    exc: BaseException,
    path: str | None = None,
) -> MonitoringDegradedError:
    if isinstance(exc, MonitoringDegradedError):
        return exc

    status_code = _status_code(exc)
    return MonitoringDegradedError(
        subsystem=subsystem,
        operation=operation,
        failure_kind=_failure_kind(exc, status_code=status_code),
        cause=exc,
        path=path,
        status_code=status_code,
    )


def warn_monitoring_issue(
    *,
    subsystem: str,
    operation: str,
    exc: BaseException,
    path: str | None = None,
    stacklevel: int = 2,
) -> MonitoringDegradedError:
    error = monitoring_error_from_exception(
        subsystem=subsystem,
        operation=operation,
        exc=exc,
        path=path,
    )
    warnings.warn(ActGuardMonitoringWarning(error), stacklevel=stacklevel)
    return error


def warn_monitoring_error(
    error: MonitoringDegradedError,
    *,
    stacklevel: int = 2,
) -> MonitoringDegradedError:
    warnings.warn(ActGuardMonitoringWarning(error), stacklevel=stacklevel)
    return error


def _status_code(exc: BaseException) -> int | None:
    for current in _error_chain(exc):
        status = getattr(current, "status_code", None)
        if isinstance(status, int):
            return status
        status = getattr(current, "status", None)
        if isinstance(status, int):
            return status
        code = getattr(current, "code", None)
        if isinstance(code, int):
            return code
    return None


def _is_ssl_cert_error(exc: BaseException) -> bool:
    """Return True if *exc* (or any chained cause) is an SSL certificate verification error."""
    for current in _error_chain(exc):
        if isinstance(current, ssl.SSLCertVerificationError):
            return True
        if isinstance(current, ssl.SSLError) and "CERTIFICATE_VERIFY_FAILED" in str(current):
            return True
        if isinstance(current, urllib.error.URLError):
            reason = current.reason
            if isinstance(reason, ssl.SSLCertVerificationError):
                return True
            if isinstance(reason, ssl.SSLError) and "CERTIFICATE_VERIFY_FAILED" in str(reason):
                return True
    return False


def _failure_kind(exc: BaseException, *, status_code: int | None) -> str:
    if status_code is not None:
        return "http"

    if _is_ssl_cert_error(exc):
        return "ssl_cert"

    for current in _error_chain(exc):
        if isinstance(current, (TimeoutError, socket.timeout)):
            return "timeout"
        if isinstance(current, urllib.error.URLError):
            reason = current.reason
            if isinstance(reason, (TimeoutError, socket.timeout)):
                return "timeout"
            if _is_connection_reason(reason):
                return "connection"
            reason_text = str(reason).lower()
            if "timed out" in reason_text or "timeout" in reason_text:
                return "timeout"
            if "refused" in reason_text or "unreachable" in reason_text:
                return "connection"
        if _is_connection_reason(current):
            return "connection"

    lowered = str(exc).lower()
    if "timed out" in lowered or "timeout" in lowered:
        return "timeout"
    return "unknown"


def _is_connection_reason(value: object) -> bool:
    if isinstance(value, ConnectionError):
        return True
    if isinstance(value, OSError) and value.errno in {
        errno.ECONNABORTED,
        errno.ECONNREFUSED,
        errno.ECONNRESET,
        errno.EHOSTDOWN,
        errno.EHOSTUNREACH,
        errno.ENETDOWN,
        errno.ENETRESET,
        errno.ENETUNREACH,
    }:
        return True
    return False


def _error_chain(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    current: BaseException | None = exc
    seen: set[int] = set()

    while current is not None and id(current) not in seen:
        chain.append(current)
        seen.add(id(current))
        next_error = getattr(current, "cause", None)
        if next_error is None:
            next_error = getattr(current, "__cause__", None)
        if next_error is None and isinstance(current, urllib.error.URLError):
            if isinstance(current.reason, BaseException):
                next_error = current.reason
        current = next_error

    return chain
