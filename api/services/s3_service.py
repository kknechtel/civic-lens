"""
Document storage service.
Uses local filesystem when AWS creds aren't configured, S3 otherwise.
"""
import os
import logging
import shutil
from pathlib import Path
from config import AWS_REGION, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, S3_BUCKET

logger = logging.getLogger(__name__)

# Local storage directory (used when no S3 creds)
LOCAL_STORAGE_DIR = os.getenv("LOCAL_STORAGE_DIR") or os.path.join(os.path.dirname(os.path.dirname(__file__)), "storage")
# STORAGE_BACKEND=local skips the S3 probe entirely (self-hosting without AWS).
STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "auto").lower()


class S3Service:
    def __init__(self):
        self.bucket = S3_BUCKET
        # Try boto3 default credential chain first (IAM role, env vars, config file)
        # Fall back to local storage only if boto3 can't connect
        self.use_local = False
        self.client = None
        self.local_dir = LOCAL_STORAGE_DIR

        if STORAGE_BACKEND == "local":
            self.use_local = True
            os.makedirs(LOCAL_STORAGE_DIR, exist_ok=True)
            return
        try:
            import boto3
            from botocore.config import Config as BotoConfig
            # Force SigV4 for presigned URLs. Buckets created post-2020 (and
            # certain regions) reject SigV2 query auth (`AWSAccessKeyId=...&Signature=...`)
            # with 400 Bad Request. SigV4 emits `X-Amz-Algorithm=AWS4-HMAC-SHA256...`.
            boto_cfg = BotoConfig(signature_version="s3v4", region_name=AWS_REGION)
            if AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY:
                self.client = boto3.client(
                    "s3",
                    region_name=AWS_REGION,
                    aws_access_key_id=AWS_ACCESS_KEY_ID,
                    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
                    config=boto_cfg,
                )
            else:
                # Use default credential chain (IAM role, ~/.aws/credentials, etc.)
                self.client = boto3.client("s3", region_name=AWS_REGION, config=boto_cfg)
            # Test connection
            self.client.head_bucket(Bucket=self.bucket)
            logger.info(f"S3Service initialized: bucket={self.bucket}, region={AWS_REGION}")
        except Exception as e:
            logger.info(f"S3 not available ({e}), using LOCAL storage: {LOCAL_STORAGE_DIR}")
            self.use_local = True
            self.client = None
            os.makedirs(LOCAL_STORAGE_DIR, exist_ok=True)

    def upload_file(self, content: bytes, key: str, content_type: str = None) -> str:
        if self.use_local:
            filepath = os.path.join(LOCAL_STORAGE_DIR, key)
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            with open(filepath, "wb") as f:
                f.write(content)
            return f"local://{key}"
        else:
            extra_args = {}
            if content_type:
                extra_args["ContentType"] = content_type
            self.client.put_object(Bucket=self.bucket, Key=key, Body=content, **extra_args)
            return f"s3://{self.bucket}/{key}"

    def register_local_file(self, local_path: str, key: str) -> str:
        """Register an existing local file without copying it. Stores the real path."""
        # Store a symlink/reference file that points to the original
        ref_path = os.path.join(LOCAL_STORAGE_DIR, key + ".ref")
        os.makedirs(os.path.dirname(ref_path), exist_ok=True)
        with open(ref_path, "w") as f:
            f.write(local_path)
        return f"local://{key}"

    def download_file(self, key: str) -> bytes:
        if self.use_local:
            # Check for reference file first
            ref_path = os.path.join(LOCAL_STORAGE_DIR, key + ".ref")
            if os.path.exists(ref_path):
                with open(ref_path, "r") as f:
                    real_path = f.read().strip()
                with open(real_path, "rb") as f:
                    return f.read()
            filepath = os.path.join(LOCAL_STORAGE_DIR, key)
            with open(filepath, "rb") as f:
                return f.read()
        else:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            return response["Body"].read()

    def get_presigned_url(self, key: str, expires_in: int = 3600) -> str:
        if self.use_local:
            # Return a local API endpoint for serving the file
            return f"/api/documents/serve/{key}"
        else:
            # Include response content-type so browser renders PDF inline
            params = {"Bucket": self.bucket, "Key": key}
            if key.lower().endswith(".pdf"):
                params["ResponseContentType"] = "application/pdf"
                params["ResponseContentDisposition"] = "inline"
            return self.client.generate_presigned_url(
                "get_object",
                Params=params,
                ExpiresIn=expires_in,
            )

    def get_presigned_upload_url(self, key: str, content_type: str = None, expires_in: int = 3600) -> str:
        """Generate a presigned PUT URL for direct browser-to-S3 upload."""
        if self.use_local:
            raise RuntimeError("Presigned upload URLs require S3 (not local storage)")
        params = {"Bucket": self.bucket, "Key": key}
        if content_type:
            params["ContentType"] = content_type
        return self.client.generate_presigned_url(
            "put_object",
            Params=params,
            ExpiresIn=expires_in,
        )

    def delete_file(self, key: str):
        if self.use_local:
            for path in [
                os.path.join(LOCAL_STORAGE_DIR, key),
                os.path.join(LOCAL_STORAGE_DIR, key + ".ref"),
            ]:
                if os.path.exists(path):
                    os.remove(path)
        else:
            self.client.delete_object(Bucket=self.bucket, Key=key)

    def list_files(self, prefix: str) -> list:
        if self.use_local:
            base = os.path.join(LOCAL_STORAGE_DIR, prefix)
            if not os.path.exists(base):
                return []
            files = []
            for root, _, fnames in os.walk(base):
                for fname in fnames:
                    if fname.endswith(".ref"):
                        continue
                    rel = os.path.relpath(os.path.join(root, fname), LOCAL_STORAGE_DIR)
                    files.append(rel.replace("\\", "/"))
            return files
        else:
            response = self.client.list_objects_v2(Bucket=self.bucket, Prefix=prefix)
            return [obj["Key"] for obj in response.get("Contents", [])]
