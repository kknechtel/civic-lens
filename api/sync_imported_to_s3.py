"""
Sync .ref-referenced files (imported docs) to S3.

The 'imported' docs are stored locally as .ref files pointing to original PDF
locations. This uploads each referenced file to S3 at the matching key.

Usage:
    python sync_imported_to_s3.py
"""
import os
import sys
import boto3
from pathlib import Path

STORAGE_DIR = os.path.join(os.path.dirname(__file__), "storage")
BUCKET = "atlantic-highlands-documents-738265942536"
REGION = "us-east-1"

s3 = boto3.client("s3", region_name=REGION)

uploaded = 0
skipped = 0
already_exists = 0
errors = []

for root, dirs, files in os.walk(STORAGE_DIR):
    for fname in files:
        if not fname.endswith(".ref"):
            continue
        ref_path = os.path.join(root, fname)
        # Build S3 key: relative to STORAGE_DIR, minus .ref suffix
        rel = os.path.relpath(ref_path, STORAGE_DIR).replace("\\", "/")
        s3_key = rel[:-len(".ref")]  # strip .ref

        # Read the ref to get original location
        with open(ref_path, "r") as f:
            local_path = f.read().strip().replace("\\", "/")

        if not os.path.isfile(local_path):
            errors.append(f"Missing source file: {local_path} (ref: {ref_path})")
            skipped += 1
            continue

        # Skip if already in S3
        try:
            s3.head_object(Bucket=BUCKET, Key=s3_key)
            already_exists += 1
            continue
        except s3.exceptions.ClientError:
            pass  # not in S3, proceed to upload

        # Upload
        try:
            content_type = "application/pdf" if s3_key.lower().endswith(".pdf") else "application/octet-stream"
            with open(local_path, "rb") as f:
                s3.put_object(
                    Bucket=BUCKET,
                    Key=s3_key,
                    Body=f,
                    ContentType=content_type,
                )
            uploaded += 1
            if uploaded % 25 == 0:
                print(f"  Uploaded {uploaded}...")
        except Exception as e:
            errors.append(f"Upload failed for {local_path}: {e}")

print()
print(f"Summary:")
print(f"  Uploaded:        {uploaded}")
print(f"  Already in S3:   {already_exists}")
print(f"  Skipped/missing: {skipped}")
print(f"  Errors:          {len(errors)}")
if errors[:10]:
    print()
    print("First errors:")
    for e in errors[:10]:
        print(f"  {e}")
