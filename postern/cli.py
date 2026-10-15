"""The `postern` command line.

  postern registry validate            check agents/registry.yaml
  postern registry compile [--write]   compile manifests; --write updates the lock file,
                                       --check fails if the lock file is stale
  postern registry sync                push the compiled registry to the control plane
  postern replay <run_id> [--json]     rebuild a run from the audit log alone
  postern audit verify                 verify the whole audit hash chain
  postern users create --email E       create a Postern user (until Phase 8's sign-in)
  postern connect link --user U --project-ref R [--api-url A]
                                       issue a single-use link to connect a project
  postern connect key --connection C   store the project's public key (hidden prompt)
  postern db-login rotate --role R --gcp-project P
                                       give a component role a new password; its
                                       connection string goes straight to Secret Manager
  postern secrets init-token-key --gcp-project P
                                       create the token-encryption key, once
  postern secrets set-supabase-client-secret --gcp-project P
                                       store the OAuth app's client secret (hidden prompt)

Database commands read the connection string from --database-url or POSTERN_DATABASE_URL.
It is never printed, and neither is any password, key or connection string these commands
create: they go to Secret Manager through gcloud's standard input.
"""

import argparse
import base64
import getpass
import os
import secrets
import subprocess
import sys
import uuid
from pathlib import Path

import httpx
import psycopg
from psycopg.types.json import Jsonb

from postern.core.audit import fetch_rows, verify_chain, verify_in_database
from postern.core.compiler import LOCK_PATH, compile_registry, render_lock
from postern.core.redaction import redact
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


def _users(args: argparse.Namespace) -> int:
    user_id = str(uuid.uuid4())
    with psycopg.connect(_database_url(args)) as conn:
        # Until Phase 8 adds sign-in with GitHub, operators create users directly.
        conn.execute(
            "insert into auth.users (id, email, aud, role, created_at, updated_at)"
            " values (%s, %s, 'authenticated', 'authenticated', now(), now())",
            (user_id, args.email),
        )
        conn.execute("insert into postern.users (id) values (%s)", (user_id,))
    print(f"created user {user_id}")
    return 0


def _connect(args: argparse.Namespace) -> int:
    from postern.integrations.supabase_mgmt.oauth import sha256_hex

    with psycopg.connect(_database_url(args)) as conn:
        if args.action == "link":
            token = secrets.token_urlsafe(32)
            conn.execute(
                "select postern.create_connect_request(%s, %s, %s, %s)",
                (args.user, args.project_ref, sha256_hex(token), args.ttl_minutes),
            )
            api_url = (args.api_url or os.environ.get("POSTERN_API_URL", "")).rstrip("/")
            print(f"single-use link (valid {args.ttl_minutes} minutes):")
            print(f"{api_url}/connect/supabase?token={token}")
            return 0

        from postern.integrations.supabase_mgmt.keys import KeyRejected, store_public_key

        # getpass: the key isn't echoed or kept in shell history.
        key = getpass.getpass("Paste the project's publishable (or anon) key: ")
        try:
            with httpx.Client(timeout=15) as http:
                kind = store_public_key(conn, args.connection, key, http)
        except KeyRejected as error:
            print(error, file=sys.stderr)
            return 1
        print(f"stored the project's {kind.replace('_', ' ')} key")
    return 0


def _gcloud(args: argparse.Namespace, *command: str, stdin: str | None = None) -> str:
    """Run gcloud against the Postern project; returns stdout. Values go in on stdin only."""
    executable = args.gcloud or os.environ.get("POSTERN_GCLOUD", "gcloud")
    result = subprocess.run(  # noqa: S603 -- fixed argument list, no shell
        [executable, *command, f"--project={args.gcp_project}", "--quiet"],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(
            f"gcloud {command[0]} {command[1]} failed: {redact(result.stderr.strip())}"
        )
    return result.stdout.strip()


def _add_secret_version(args: argparse.Namespace, secret: str, value: str) -> str:
    return _gcloud(
        args,
        "secrets",
        "versions",
        "add",
        secret,
        "--data-file=-",
        "--format=value(name)",
        stdin=value,
    )


def _db_login(args: argparse.Namespace) -> int:
    from psycopg import sql

    from postern.core.db_logins import (
        COMPONENT_ROLES,
        component_url,
        new_password,
        scram_sha256_verifier,
    )

    secret = COMPONENT_ROLES[args.role]
    operator_url = _database_url(args)
    password = new_password()
    url = component_url(operator_url, args.role, password)
    # One transaction: if storing the secret fails, the password change rolls back.
    with psycopg.connect(operator_url) as conn:
        conn.execute(
            sql.SQL("alter role {} with login password {}").format(
                sql.Identifier(args.role), sql.Literal(scram_sha256_verifier(password))
            )
        )
        version = _add_secret_version(args, secret, url)
    with psycopg.connect(url, prepare_threshold=None) as check:
        row = check.execute("select current_user").fetchone()
    if row is None or row[0] != args.role:
        print(f"{args.role}: password set, but logging in with it failed", file=sys.stderr)
        return 1
    print(f"{args.role}: new password set and verified; connection string stored as {version}")
    return 0


def _secrets(args: argparse.Namespace) -> int:
    if args.action == "set-supabase-client-secret":
        # Hidden prompt: the secret isn't echoed, kept in history, or given a trailing newline.
        value = getpass.getpass("Paste the Supabase OAuth app's client secret: ").strip()
        if not value:
            print("nothing entered; nothing stored", file=sys.stderr)
            return 1
        version = _add_secret_version(args, "supabase-oauth-client-secret", value)
        print(f"stored the client secret as {version}")
        return 0

    secret = "token-encryption-key"  # noqa: S105 -- the name of the secret, not its value
    existing = _gcloud(
        args,
        "secrets",
        "versions",
        "list",
        secret,
        "--filter=state=ENABLED",
        "--format=value(name)",
    )
    if existing:
        print(
            f"{secret} already has a key. Replacing it would make every stored OAuth token"
            " unreadable, and Postern doesn't rotate this key yet.",
            file=sys.stderr,
        )
        return 1
    version = _add_secret_version(args, secret, base64.b64encode(os.urandom(32)).decode())
    print(f"created {secret} as {version}")
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

    users = commands.add_parser("users", help="manage Postern users")
    users.add_argument("action", choices=["create"])
    users.add_argument("--email", required=True)
    users.add_argument("--database-url")
    users.set_defaults(handler=_users)

    connect = commands.add_parser("connect", help="connect a Supabase project")
    connect.add_argument("action", choices=["link", "key"])
    connect.add_argument("--user")
    connect.add_argument("--project-ref")
    connect.add_argument("--connection")
    connect.add_argument("--api-url")
    connect.add_argument("--ttl-minutes", type=int, default=60)
    connect.add_argument("--database-url")
    connect.set_defaults(handler=_connect)

    db_login = commands.add_parser("db-login", help="manage component database logins")
    db_login.add_argument("action", choices=["rotate"])
    db_login.add_argument("--role", required=True, choices=["postern_api", "postern_worker"])
    db_login.add_argument("--gcp-project", required=True)
    db_login.add_argument("--gcloud", help="path to gcloud (default: gcloud on PATH)")
    db_login.add_argument("--database-url")
    db_login.set_defaults(handler=_db_login)

    secrets_cmd = commands.add_parser("secrets", help="create Postern's own keys")
    secrets_cmd.add_argument("action", choices=["init-token-key", "set-supabase-client-secret"])
    secrets_cmd.add_argument("--gcp-project", required=True)
    secrets_cmd.add_argument("--gcloud", help="path to gcloud (default: gcloud on PATH)")
    secrets_cmd.set_defaults(handler=_secrets)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result: int = args.handler(args)
    return result


if __name__ == "__main__":
    sys.exit(main())
