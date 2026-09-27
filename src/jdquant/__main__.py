"""Command-line entry point: `python -m jdquant serve | create-user`."""

from __future__ import annotations

import argparse
import getpass
import os
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jdquant")
    parser.add_argument("--data-dir", default=os.environ.get("JDQ_DATA_DIR", "data"))
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the API and web UI")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)

    user = sub.add_parser("create-user", help="create a user (the first user may be given all roles)")
    user.add_argument("email")
    user.add_argument("--name", default=None)
    user.add_argument("--roles", default="", help="comma-separated roles; empty with --owner for all roles")
    user.add_argument("--owner", action="store_true", help="grant every built-in role")

    args = parser.parse_args(argv)
    os.environ["JDQ_DATA_DIR"] = args.data_dir

    if args.command == "serve":
        import uvicorn

        uvicorn.run("jdquant.api.app:app_factory", factory=True, host=args.host, port=args.port)
        return 0

    from jdquant.api.context import build_context
    from jdquant.security.permissions import ROLES

    context = build_context()
    roles = sorted(ROLES) if args.owner else [r for r in args.roles.split(",") if r]
    password = os.environ.get("JDQ_NEW_USER_PASSWORD") or getpass.getpass("Password: ")
    user_obj = context.identity.create_user(args.email, args.name or args.email, password, roles, actor="cli")
    print(f"created {user_obj.email} ({user_obj.user_id}) with roles {', '.join(roles) or '-'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
