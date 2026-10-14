"""The `postern` command line.

  postern registry validate            check agents/registry.yaml
  postern registry compile [--write]   compile manifests; --write updates the lock file,
                                       --check fails if the lock file is stale
  postern registry sync                push the compiled registry to the control plane
  postern replay <run_id> [--json]     rebuild a run from the audit log alone
  postern audit verify                 verify the whole audit hash chain

Database commands read the connection string from --database-url or POSTERN_DATABASE_URL.
It is never printed.
"""

import argparse
import os
import sys
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from postern.core.audit import fetch_rows, verify_chain, verify_in_database
from postern.core.compiler import LOCK_PATH, compile_registry, render_lock
from postern.core.registry import REGISTRY_PATH, RegistryError, load_registry
from postern.core.replay import ReplayError, render_json, render_text, replay_run


def _database_url(args: argparse.Namespace) -> str:
    url = args.database_url or os.environ.get("POSTERN_DATABASE_URL")
    if not url:
        raise SystemExit("set POSTERN_DATABASE_URL or pass --database-url")
    return str(url)


def _registry(args: argparse.Namespace) -> int:
    try:
        registry = load_registry(Path(args.registry))
    except RegistryError as error:
        print(error, file=sys.stderr)
        return 1
    compiled = compile_registry(registry)

    if args.action == "validate":
        print(f"registry ok: {len(compiled)} agents")
        return 0

    if args.action == "compile":
        lock = render_lock(compiled)
        lock_path = Path(args.lock)
        if args.write:
            lock_path.write_text(lock, encoding="utf-8", newline="\n")
            print(f"wrote {lock_path}")
        elif args.check:
            current = lock_path.read_text(encoding="utf-8") if lock_path.exists() else ""
            if current != lock:
                print(
                    f"{lock_path} is stale: run `postern registry compile --write`", file=sys.stderr
                )
                return 1
            print(f"{lock_path} is up to date")
        else:
            print(lock, end="")
        return 0

    # sync: one transaction; every grant change is audited by postern.sync_agent.
    with psycopg.connect(_database_url(args)) as conn:
        for name, agent in compiled.items():
            spec = agent.spec
            row = conn.execute(
                "select postern.sync_agent(%s, %s, %s::smallint, %s::text[], %s, %s, %s,"
                " %s::postern.agent_status, %s, %s, %s)",
                (
                    name,
                    spec.owner,
                    spec.tier,
                    sorted(spec.tools),
                    spec.human_gate,
                    Jsonb(spec.notification_budget.model_dump()),
                    spec.schedule,
                    spec.status,
                    Jsonb(dict(agent.manifest)),
                    agent.manifest_hash,
                    registry.version,
                ),
            ).fetchone()
            print(f"{name:22} {row[0] if row else '?'}")
        retired = conn.execute(
            "select postern.retire_agents_not_in(%s::text[])", (sorted(compiled),)
        ).fetchone()
        print(f"retired: {retired[0] if retired else 0}")
    return 0


def _replay(args: argparse.Namespace) -> int:
    with psycopg.connect(_database_url(args)) as conn:
        try:
            run = replay_run(conn, args.run_id)
        except ReplayError as error:
            print(f"cannot replay {args.run_id}: {error}", file=sys.stderr)
            return 1
    print(render_json(run) if args.json else render_text(run))
    return 0 if not run.integrity_problems else 2


def _audit(args: argparse.Namespace) -> int:
    with psycopg.connect(_database_url(args)) as conn:
        rows = fetch_rows(conn)
        problems = verify_chain(rows) + verify_in_database(conn)
    if problems:
        for problem in problems:
            print(f"#{problem.chain_position}: {problem.problem}")
        return 2
    print(f"audit chain intact: {len(rows)} rows verified in Python and in the database")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="postern")
    commands = parser.add_subparsers(dest="command", required=True)

    registry = commands.add_parser("registry", help="validate, compile or sync the agent registry")
    registry.add_argument("action", choices=["validate", "compile", "sync"])
    registry.add_argument("--registry", default=str(REGISTRY_PATH))
    registry.add_argument("--lock", default=str(LOCK_PATH))
    mode = registry.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="compile: update the lock file")
    mode.add_argument("--check", action="store_true", help="compile: fail if the lock is stale")
    registry.add_argument("--database-url")
    registry.set_defaults(handler=_registry)

    replay = commands.add_parser("replay", help="rebuild a run from the audit log alone")
    replay.add_argument("run_id")
    replay.add_argument("--json", action="store_true")
    replay.add_argument("--database-url")
    replay.set_defaults(handler=_replay)

    audit = commands.add_parser("audit", help="verify the audit hash chain")
    audit.add_argument("action", choices=["verify"])
    audit.add_argument("--database-url")
    audit.set_defaults(handler=_audit)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result: int = args.handler(args)
    return result


if __name__ == "__main__":
    sys.exit(main())
