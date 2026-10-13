"""docs/threat-model.md and the threat-model tests must stay in one-to-one correspondence."""

import ast
import re
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
THREAT_MODEL = REPO_ROOT / "docs" / "threat-model.md"
TEST_DIRS = ("tests/invariants", "tests/credential_audit")
MIN_FAILURE_MODES = 15

SECTION = re.compile(r"^### (TM-\d{2}) · (.+)$", re.MULTILINE)
REF = re.compile(r"→ `(tests/(?:invariants|credential_audit)/test_\w+\.py)::(test_\w+)`")
TM_ID = re.compile(r"TM-\d{2}")
REQUIRED_FIELDS = (
    "**Impact:**",
    "**Likelihood:**",
    "**Structural prevention:**",
    "**Detection:**",
    "**Lands in:**",
)


def _threat_model() -> str:
    return THREAT_MODEL.read_text(encoding="utf-8")


def _sections() -> list[tuple[str, str]]:
    """(TM id, section body) in document order."""
    parts = SECTION.split(_threat_model())
    return [
        (tm_id, body.split("\n## ", 1)[0])
        for tm_id, body in zip(parts[1::3], parts[3::3], strict=True)
    ]


def _referenced_tests() -> dict[str, set[str]]:
    """Test id (path::name) -> ids of the failure modes whose preventions name it."""
    refs: dict[str, set[str]] = defaultdict(set)
    for tm_id, body in _sections():
        for path, name in REF.findall(body):
            refs[f"{path}::{name}"].add(tm_id)
    return refs


def _defined_tests() -> dict[str, ast.FunctionDef]:
    tests = {}
    for directory in TEST_DIRS:
        for file in sorted((REPO_ROOT / directory).glob("test_*.py")):
            for node in ast.parse(file.read_text(encoding="utf-8")).body:
                if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                    tests[f"{directory}/{file.name}::{node.name}"] = node
    return tests


def _xfail_reason(node: ast.FunctionDef) -> str | None:
    for decorator in node.decorator_list:
        if (
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and decorator.func.attr == "xfail"
        ):
            for keyword in decorator.keywords:
                if keyword.arg == "reason" and isinstance(keyword.value, ast.Constant):
                    return str(keyword.value.value)
    return None


def test_threat_model_lists_enough_failure_modes() -> None:
    assert len(_sections()) >= MIN_FAILURE_MODES


def test_failure_mode_ids_are_sequential() -> None:
    ids = [tm_id for tm_id, _ in _sections()]
    assert ids == [f"TM-{i:02d}" for i in range(1, len(ids) + 1)]


def test_every_failure_mode_has_every_field_and_a_test() -> None:
    for tm_id, body in _sections():
        missing = [field for field in REQUIRED_FIELDS if field not in body]
        assert not missing, f"{tm_id} is missing {missing}"
        prevention = body.split("**Structural prevention:**", 1)[1].split("**Detection:**", 1)[0]
        assert REF.search(prevention), f"{tm_id} names no test under Structural prevention"


def test_summary_table_lists_every_failure_mode() -> None:
    text = _threat_model()
    for tm_id, _ in _sections():
        assert f"| {tm_id} |" in text, f"{tm_id} is missing from the summary table"


def test_every_named_test_exists() -> None:
    missing = sorted(_referenced_tests().keys() - _defined_tests().keys())
    assert not missing, "named in docs/threat-model.md but not defined:\n" + "\n".join(missing)


def test_every_threat_model_test_is_named_in_the_threat_model() -> None:
    orphans = sorted(_defined_tests().keys() - _referenced_tests().keys())
    assert not orphans, "defined but not named in docs/threat-model.md:\n" + "\n".join(orphans)


def test_threat_model_test_names_are_unique() -> None:
    names = [test_id.split("::")[1] for test_id in _defined_tests()]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    assert not duplicates, f"test names must be unique across files: {duplicates}"


def test_xfail_reasons_cite_the_failure_modes_that_name_them() -> None:
    referenced = _referenced_tests()
    for test_id, node in _defined_tests().items():
        reason = _xfail_reason(node)
        if reason is not None:
            cited = set(TM_ID.findall(reason))
            assert cited == referenced[test_id], f"{test_id}: reason cites {sorted(cited)}"
