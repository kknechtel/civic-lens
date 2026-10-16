"""
Set up / update AWS Secrets Manager for Atlantic Highlands production.

Idempotent: reads the existing secret first and only overwrites keys you
explicitly pass via env vars. Re-running with no env vars is a no-op (won't
rotate JWT_SECRET and invalidate every live session).

Usage:
    # First-time bootstrap — provide everything via env:
    RDS_PASSWORD='...' ANTHROPIC_API_KEY='...' GEMINI_API_KEY='...' \
        python setup_secrets.py

    # Rotate just the RDS password after rotating it in the RDS console:
    RDS_PASSWORD='<new-value>' python setup_secrets.py

The RDS password is NEVER hardcoded here. It must come from RDS_PASSWORD env
or already exist in the secret. The historical value AH-Docs-2026! was
committed in this file (commit c578467) and must be considered burned —
rotate it in the RDS console before running this.
"""
import json
import os
import secrets
import sys
import boto3

SECRET_NAME = "atlantic-highlands/production"
REGION = "us-east-1"
RDS_HOST = "atlantic-highlands-db.c4xoyiqaey7u.us-east-1.rds.amazonaws.com"
RDS_USER = "ahAdmin"
RDS_DB = "atlantic_highlands"

# Static (non-secret) config that's always safe to overwrite.
STATIC = {
    "JWT_EXPIRATION_HOURS": "24",
    "ALLOWED_ORIGINS": "https://ahnj.info,https://www.ahnj.info,https://atlantic-highlands.amplifyapp.com,http://localhost:3000",
    "S3_BUCKET": "atlantic-highlands-documents-738265942536",
    "AWS_REGION": REGION,
    "DEBUG": "false",
}

client = boto3.client("secretsmanager", region_name=REGION)

# Load existing secret (empty dict if it doesn't exist yet)
try:
    resp = client.get_secret_value(SecretId=SECRET_NAME)
    existing = json.loads(resp["SecretString"])
    creating = False
    print(f"Loaded existing secret: {SECRET_NAME} ({len(existing)} keys)")
except client.exceptions.ResourceNotFoundException:
    existing = {}
    creating = True
    print(f"Secret {SECRET_NAME} does not exist yet — will create.")

# Start from existing, then apply updates.
merged = dict(existing)
merged.update(STATIC)

# RDS password: env wins; otherwise keep existing DATABASE_URL untouched.
rds_password = os.environ.get("RDS_PASSWORD")
if rds_password:
    merged["DATABASE_URL"] = (
        f"postgresql://{RDS_USER}:{rds_password}@{RDS_HOST}:5432/{RDS_DB}"
    )
    # Also store the raw password so sync_to_aws.sh (which can't easily parse
    # a URL in bash) reads it from the same secret as the app — one place to
    # rotate, no cross-project secret coupling.
    merged["RDS_PASSWORD"] = rds_password
    print("Updated DATABASE_URL and RDS_PASSWORD with new value")
elif "DATABASE_URL" not in merged:
    print("ERROR: no existing DATABASE_URL and no RDS_PASSWORD env var.", file=sys.stderr)
    print("       Set RDS_PASSWORD='<value>' and re-run.", file=sys.stderr)
    sys.exit(1)

# JWT_SECRET / SECRET_KEY: only generate on first creation. Re-running with
# fresh values would log everyone out and break alert-digest unsubscribe links.
if "JWT_SECRET" not in merged:
    merged["JWT_SECRET"] = secrets.token_urlsafe(48)
    print("Generated new JWT_SECRET")
if "SECRET_KEY" not in merged:
    merged["SECRET_KEY"] = secrets.token_urlsafe(48)
    print("Generated new SECRET_KEY")

# API keys: env overrides; otherwise keep existing. Empty string is treated
# as "don't change" so re-running without these env vars is safe.
for key in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "VOYAGE_API_KEY", "OPENAI_API_KEY"):
    val = os.environ.get(key)
    if val:
        merged[key] = val
        print(f"Updated {key}")
    elif key not in merged:
        merged[key] = ""  # Initialize empty so config._get() returns "" cleanly

if creating:
    client.create_secret(
        Name=SECRET_NAME,
        Description="Atlantic Highlands production configuration",
        SecretString=json.dumps(merged),
    )
    print(f"\nCreated secret: {SECRET_NAME}")
else:
    client.put_secret_value(SecretId=SECRET_NAME, SecretString=json.dumps(merged))
    print(f"\nUpdated secret: {SECRET_NAME}")

print(f"\nKeys present: {sorted(merged.keys())}")
print(f"\nNext: restart the API so it picks up the new values:")
print(f"  aws ssm send-command --instance-ids i-06424a799368c7d6d \\")
print(f"    --document-name AWS-RunShellScript \\")
print(f"    --parameters 'commands=[\"systemctl restart ah-api\"]'")
