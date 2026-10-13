"""Threat-model tests: TM-05, TM-07. See docs/threat-model.md."""

import base64
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Patterns are assembled from pieces so this file doesn't match itself.
PRIVATE_KEY = re.compile("-----BEGIN [A-Z ]*" + "PRIVATE KEY-----")
SUPABASE_SECRET_KEY = re.compile("sb_" + r"secret_[A-Za-z0-9_\-]{16,}")
JWT = re.compile(r"eyJ[A-Za-z0-9_\-]+\.(eyJ[A-Za-z0-9_\-]+)\.[A-Za-z0-9_\-]+")
ENV_FILE = re.compile(r"(^|/)\.env(\..+)?$")


def _committable_files() -> list[str]:
    """Tracked files plus untracked files that .gitignore doesn't exclude."""
    git = shutil.which("git")
    assert git, "git is required for this test"
    out = subprocess.run(  # noqa: S603 -- fixed arguments, no user input
        [git, "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    return [p for p in out.split("\0") if p]


def _is_service_role_jwt(payload: str) -> bool:
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except ValueError:
        return False
    return isinstance(claims, dict) and claims.get("role") == "service_role"


def test_no_secrets_or_env_files_committed() -> None:
    """No .env files, private keys or secret-shaped strings are committed. Phase 0 adds pre-commit
    hooks for this; the test proves the repo is clean."""
    problems = []
    for rel in _committable_files():
        if ENV_FILE.search(rel) and not rel.endswith(".env.example"):
            problems.append(f"{rel}: .env file")
            continue
        path = REPO_ROOT / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if PRIVATE_KEY.search(text):
            problems.append(f"{rel}: private key")
        if SUPABASE_SECRET_KEY.search(text):
            problems.append(f"{rel}: Supabase secret key")
        if any(_is_service_role_jwt(p) for p in JWT.findall(text)):
            problems.append(f"{rel}: service_role JWT")
    assert not problems, "\n".join(problems)


@pytest.mark.xfail(
    raises=NotImplementedError, strict=True, reason="TM-07 · prevention lands in Phase 1"
)
def test_no_user_database_passwords_or_service_role_keys_in_config() -> None:
    """Config, code and Secret Manager contain no user database password, service_role key or
    secret key."""
    raise NotImplementedError
