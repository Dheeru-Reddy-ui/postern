"""Threat-model tests: TM-05, TM-07. See docs/threat-model.md."""

import base64
import json
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
TERRAFORM_ROOTS = (
    "infra/terraform/gcp/bootstrap",
    "infra/terraform/gcp",
    "infra/terraform/supabase",
)

# Invariant 1: Postern never holds a user's service_role key, secret key or DB password.
FORBIDDEN_CREDENTIAL_NAME = re.compile(
    r"service[_-]?role|sb[_-]?secret|secret[_-]?(api[_-]?)?key|db[_-]?pass|database[_-]?pass",
    re.IGNORECASE,
)
# Postern's own control plane is not a user's database.
POSTERN_OWN_CREDENTIALS = {"control-plane-db-url-api", "control-plane-db-url-worker"}

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


def _read_all(pattern: str) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(REPO_ROOT.glob(pattern)))


def _config_names(terraform: Callable[[str], Any]) -> dict[str, str]:
    """Every name that could carry a credential into Postern: secret IDs, Terraform variables,
    container and workflow environment variables. Values say where each name came from."""
    names: dict[str, str] = {}
    access = REPO_ROOT / "infra" / "terraform" / "gcp" / "secret_access.json"
    for secret in json.loads(access.read_text(encoding="utf-8"))["secrets"]:
        names[secret] = "secret_access.json"
    for root in TERRAFORM_ROOTS:
        for variable in terraform(root).variables():
            names[variable] = f"{root} variable"
    for match in re.finditer(r'name\s*=\s*"([A-Z0-9_]+)"', _read_all("infra/terraform/**/*.tf")):
        names[match.group(1)] = "Cloud Run env"
    for match in re.finditer(r"^ENV\s+(.+)$", _read_all("Dockerfile"), re.MULTILINE):
        for key in re.findall(r"([A-Z][A-Z0-9_]+)=", match.group(1)):
            names[key] = "Dockerfile ENV"
    workflows = _read_all(".github/workflows/*.y*ml")
    for match in re.finditer(r"(?:vars|secrets)\.([A-Za-z0-9_]+)", workflows):
        names[match.group(1)] = "workflow"
    return names


def test_no_user_database_passwords_or_service_role_keys_in_config(
    terraform: Callable[[str], Any],
) -> None:
    """Config, code and Secret Manager contain no user database password, service_role key or
    secret key. Postern's own control-plane connection strings are allowed by name."""
    names = _config_names(terraform)
    assert names, "found no configuration names to check"
    offenders = {
        name: source
        for name, source in names.items()
        if FORBIDDEN_CREDENTIAL_NAME.search(name) and name not in POSTERN_OWN_CREDENTIALS
    }
    assert not offenders, f"names that suggest a user credential: {offenders}"
