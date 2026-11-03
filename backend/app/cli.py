"""Admin CLI.

  python -m app.cli sync-permissions
  python -m app.cli create-admin --email admin@example.com [--name "Ada"]   (prompts for password)
  python -m app.cli health beat [--max-age 120]     exit 1 if the scheduler has not dispatched recently
  python -m app.cli check-config                     validate production settings (exit 1 on errors)
"""
import argparse
import getpass
import sys

from app.auth.permissions import SUPER_ADMIN, sync_roles_and_permissions
from app.db import SessionLocal
from app.services.user_service import create_user


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sync-permissions")
    ca = sub.add_parser("create-admin")
    ca.add_argument("--email", required=True)
    ca.add_argument("--name", default="")
    ca.add_argument("--password-stdin", action="store_true", help="read password from stdin")
    hp = sub.add_parser("health")
    hp.add_argument("component", choices=["beat"])
    hp.add_argument("--max-age", type=float, default=120.0)
    sub.add_parser("check-config")
    args = parser.parse_args(argv)

    if args.cmd == "health":
        import time

        from app.auth.ratelimit import get_redis
        from app.worker.tasks import BEAT_KEY

        try:
            v = get_redis().get(BEAT_KEY)
        except Exception as e:  # noqa: BLE001
            print(f"unhealthy: redis error {e}", file=sys.stderr)
            return 1
        age = time.time() - float(v) if v else None
        if age is None or age > args.max_age:
            print(f"unhealthy: last dispatch {'never' if age is None else f'{age:.0f}s ago'}", file=sys.stderr)
            return 1
        print(f"healthy: last dispatch {age:.0f}s ago")
        return 0
    if args.cmd == "check-config":
        from app.observability import ConfigError, check_production_settings

        try:
            for w in check_production_settings():
                print(f"warning: {w}")
        except ConfigError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        print("configuration ok")
        return 0

    with SessionLocal() as db:
        if args.cmd == "sync-permissions":
            sync_roles_and_permissions(db)
            db.commit()
            print("Roles and permissions synchronised.")
        elif args.cmd == "create-admin":
            if args.password_stdin:
                pw = sys.stdin.readline().rstrip("\n")
            else:
                pw = getpass.getpass("Password: ")
                if pw != getpass.getpass("Confirm: "):
                    print("Passwords do not match.", file=sys.stderr)
                    return 1
            try:
                u = create_user(db, args.email, pw, SUPER_ADMIN, args.name)
            except Exception as e:  # HTTPException from validation
                print(f"Error: {getattr(e, 'detail', e)}", file=sys.stderr)
                return 1
            db.commit()
            print(f"Created super administrator {u.email}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
