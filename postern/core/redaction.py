"""Keep secrets out of logs and prompts (TM-05).

`find_secrets` recognizes credential-shaped strings. Logs pass through `RedactingFilter`, and
the LLM gateway refuses any prompt in which `find_secrets` finds something.
"""

import base64
import json
import logging
import re
from typing import Any

# Assembled from pieces so this module doesn't match its own source.
_PATTERNS: dict[str, re.Pattern[str]] = {
    "supabase_secret_key": re.compile("sb_" + r"secret_[A-Za-z0-9_\-]{12,}"),
    "supabase_access_token": re.compile("sb" + r"p_[0-9a-f]{40}"),
    "supabase_oauth_token": re.compile("sb" + r"[ar]_[A-Za-z0-9_\-]{20,}"),
    "private_key": re.compile("-----BEGIN [A-Z ]*" + "PRIVATE KEY-----"),
    "google_api_key": re.compile("AI" + r"za[0-9A-Za-z_\-]{35}"),
    "bearer_token": re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{20,}"),
    "password_in_url": re.compile(r"://[^/\s:@]+:[^@\s]{6,}@"),
}
_JWT = re.compile(r"eyJ[A-Za-z0-9_\-]+\.(eyJ[A-Za-z0-9_\-]+)\.[A-Za-z0-9_\-]+")
REDACTED = "[REDACTED]"


def _jwt_is_privileged(payload: str) -> bool:
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except ValueError:
        return True  # unreadable token: treat as sensitive
    return not (isinstance(claims, dict) and claims.get("role") == "anon")


def find_secrets(text: str) -> list[str]:
    """Names of the credential kinds found in text (empty when clean)."""
    found = [name for name, pattern in _PATTERNS.items() if pattern.search(text)]
    if any(_jwt_is_privileged(match) for match in _JWT.findall(text)):
        found.append("privileged_jwt")
    return found


def redact(text: str) -> str:
    for pattern in _PATTERNS.values():
        text = pattern.sub(REDACTED, text)
    return _JWT.sub(lambda m: REDACTED if _jwt_is_privileged(m.group(1)) else m.group(0), text)


class RedactingFilter(logging.Filter):
    """Redacts credentials from every log record's message and arguments."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(str(record.msg))
        if record.args:
            args: Any = record.args
            if isinstance(args, dict):
                record.args = {k: redact(v) if isinstance(v, str) else v for k, v in args.items()}
            else:
                record.args = tuple(redact(a) if isinstance(a, str) else a for a in args)
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line, which Cloud Logging parses into a structured entry."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "severity": record.levelname if record.levelname != "CRITICAL" else "ALERT",
            "message": record.getMessage(),
            "logger": record.name,
        }
        for key in ("run_id", "agent", "postern_alert", "component"):
            if hasattr(record, key):
                entry[key] = getattr(record, key)
        return redact(json.dumps(entry, default=str))


def configure_logging(component: str) -> logging.Logger:
    def tag(record: logging.LogRecord) -> bool:
        record.component = component
        return True

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactingFilter())
    handler.addFilter(tag)
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    return logging.getLogger(f"postern.{component}")
