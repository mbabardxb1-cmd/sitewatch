"""Admin CLI.

  python -m app.cli sync-permissions
  python -m app.cli create-admin --email admin@example.com [--name "Ada"]   (prompts for password)
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
    args = parser.parse_args(argv)

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
