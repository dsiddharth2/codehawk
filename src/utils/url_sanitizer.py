"""
URL Sanitizer Utility.

Provides functions to sanitize URLs by removing credentials and redacting
sensitive query parameters before logging or displaying them.
"""

from typing import Any, Dict
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse


def sanitize_sensitive_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Sanitize a dictionary by redacting values for keys that may contain secrets.

    Args:
        data: Dictionary to sanitize

    Returns:
        New dictionary with sensitive values redacted
    """
    sensitive_keywords = (
        'token', 'pat', 'authorization', 'password', 'secret',
        'apikey', 'api_key', 'auth', 'bearer', 'credential'
    )

    sanitized = {}
    for key, value in data.items():
        key_lower = str(key).lower()
        if any(keyword in key_lower for keyword in sensitive_keywords):
            sanitized[key] = '***'
        elif isinstance(value, str) and any(keyword in value.lower() for keyword in ('bearer ', 'token=', 'pat=')):
            sanitized[key] = '***'
        else:
            sanitized[key] = value

    return sanitized


def sanitize_url(value: str) -> str:
    """
    Sanitize URL by removing credentials and redacting query parameters.

    Args:
        value: URL to sanitize

    Returns:
        Sanitized URL with credentials removed and query params redacted
    """
    try:
        p = urlparse(value)
        hostname = p.hostname or ""
        netloc = f"{hostname}:{p.port}" if p.port else hostname
        q = parse_qs(p.query, keep_blank_values=True)
        redacted = {k: ["***"] * len(v) for k, v in q.items()}
        return urlunparse((p.scheme, netloc, p.path, p.params, urlencode(redacted, doseq=True), p.fragment))
    except Exception:
        return "<redacted>"
