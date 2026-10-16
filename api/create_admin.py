"""Create or update an admin user. Run once to bootstrap authentication.

Usage:
    python create_admin.py admin@example.org              # prints a generated password
    python create_admin.py admin@example.org 'pass phrase' # explicit password

The account is flagged must_change_password, so the first login forces a new one.
"""
import secrets
import sys

from database import SessionLocal, init_db
from models.user import User
from auth import hash_password
from municipality import get_municipality

if len(sys.argv) < 2:
    sys.exit(__doc__)

email = sys.argv[1].strip().lower()
password = sys.argv[2] if len(sys.argv) > 2 else secrets.token_urlsafe(12)

init_db()
db = SessionLocal()

existing = db.query(User).filter(User.email == email).first()
if existing:
    existing.hashed_password = hash_password(password)
    existing.is_admin = True
    existing.is_active = True
    existing.must_change_password = True
    db.commit()
    print(f"Updated existing user: {email} (admin=True, active=True, password reset)")
else:
    db.add(User(
        email=email,
        username=email.split("@")[0],
        hashed_password=hash_password(password),
        is_active=True,
        is_admin=True,
        must_change_password=True,
    ))
    db.commit()
    print(f"Created admin user: {email}")

if len(sys.argv) <= 2:
    print(f"Temporary password: {password}")
print(f"\nLogin at: {get_municipality().branding.public_origin}")
db.close()
