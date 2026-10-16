"""
Create a one-time invite link (the only way into an invite_only deployment
besides an admin-created account).

    DEPLOYMENT=long-beach-ny PUBLIC_ORIGIN=https://longbeach.ahnj.info \\
        python -m scripts.create_invite --days 7
    python -m scripts.create_invite --email friend@example.org   # only that address can use it

The link works once. Anyone holding it before then can use it, so send it
privately.
"""
from __future__ import annotations

import argparse
import secrets
import sys
from datetime import datetime, timedelta


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", default=None, help="lock the invite to this address")
    ap.add_argument("--days", type=int, default=7)
    args = ap.parse_args()
    from database import SessionLocal, init_db
    from models.user import InviteToken, User
    from municipality import get_municipality

    init_db()
    with SessionLocal() as db:
        admin = db.query(User).filter(User.is_admin.is_(True)).order_by(User.created_at).first()
        if not admin:
            print("Create an admin first: python create_admin.py you@example.org", file=sys.stderr)
            return 2
        token = secrets.token_urlsafe(32)
        expires = datetime.utcnow() + timedelta(days=args.days)
        db.add(InviteToken(token=token, email=args.email.lower() if args.email else None,
                           created_by=admin.id, expires_at=expires))
        db.commit()
    origin = get_municipality().branding.public_origin.rstrip("/")
    print(f"{origin}?invite={token}")
    print(f"(one use; expires {expires:%Y-%m-%d %H:%M} UTC{'; only for ' + args.email if args.email else ''})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
