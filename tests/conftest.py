"""Shared fixtures."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import hcl2
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _unquote(text: str) -> str:
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return text[1:-1]
    return text


def _clean(value: Any) -> Any:
    """python-hcl2 keeps HCL quoting and parser metadata; strip both to get plain values."""
    if isinstance(value, dict):
        return {
            _unquote(key): _clean(item)
            for key, item in value.items()
            if key not in ("__comments__", "__is_block__")
        }
    if isinstance(value, list):
        return [_clean(item) for item in value]
    if isinstance(value, str):
        return _unquote(value)
    return value


class TerraformRoot:
    """Every *.tf file in one Terraform root module, parsed and merged."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.blocks: dict[str, list[Any]] = {}
        for path in sorted(directory.glob("*.tf")):
            with path.open(encoding="utf-8") as handle:
                parsed = _clean(hcl2.load(handle))
            for kind, entries in parsed.items():
                self.blocks.setdefault(kind, []).extend(entries)

    def resources(self, resource_type: str) -> dict[str, dict[str, Any]]:
        """Resource name -> body, for every resource of one type."""
        found: dict[str, dict[str, Any]] = {}
        for entry in self.blocks.get("resource", []):
            for name, body in entry.get(resource_type, {}).items():
                found[name] = body
        return found

    def resource_types(self) -> set[str]:
        return {kind for entry in self.blocks.get("resource", []) for kind in entry}

    def variables(self) -> dict[str, dict[str, Any]]:
        return {
            name: body for entry in self.blocks.get("variable", []) for name, body in entry.items()
        }

    def locals(self) -> dict[str, Any]:
        return {
            key: value for entry in self.blocks.get("locals", []) for key, value in entry.items()
        }


@pytest.fixture(scope="session")
def terraform() -> Callable[[str], TerraformRoot]:
    """Parse a Terraform root module by its path relative to the repo root."""
    cache: dict[str, TerraformRoot] = {}

    def load(relative: str) -> TerraformRoot:
        if relative not in cache:
            cache[relative] = TerraformRoot(REPO_ROOT / relative)
        return cache[relative]

    return load
