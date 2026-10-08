"""Operator commands: the person with the table's database file grants roles and revokes tokens.

Privileged roles (admin, lead) are never self-assigned through the MCP tools: an agent asks,
the operator decides.

    python -m team_table.admin list
    python -m team_table.admin grant NAME ROLE
        (ROLE: admin, lead, agent, coder, reviewer, designer, tester)
    python -m team_table.admin revoke-tokens NAME
        (the agent must register again with a new token)
"""

from __future__ import annotations

import argparse
import sys

from team_table.config import Config
from team_table.db import Database
from team_table.validation import ValidationError, validate_role


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m team_table.admin", description=__doc__.splitlines()[0]
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="members and their roles")
    g = sub.add_parser("grant", help="set a member's role")
    g.add_argument("name")
    g.add_argument("role")
    r = sub.add_parser("revoke-tokens", help="revoke all of a member's tokens")
    r.add_argument("name")
    args = ap.parse_args(argv)
    db = Database(Config.from_env())
    if args.cmd == "list":
        for m in db.list_members(include_inactive=True):
            print(f"{m['name']:30} {m['role']:10} {m['status']}")
        return 0
    if not db.member_exists(args.name):
        print(f"No member named {args.name!r}", file=sys.stderr)
        return 1
    if args.cmd == "grant":
        try:
            validate_role(args.role)
        except ValidationError as e:
            print(e.message, file=sys.stderr)
            return 1
        conn = db._get_conn()
        conn.execute("UPDATE members SET role=? WHERE name=?", (args.role, args.name))
        db.log_action("operator", "grant_role", "member", args.name, {"role": args.role})
        conn.commit()
        print(f"{args.name} is now {args.role}")
        return 0
    n = db.revoke_tokens(args.name)
    print(f"Revoked {n} token(s) for {args.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
