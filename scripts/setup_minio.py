from minio import Minio
from minio.error import S3Error
import os

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ROOT_USER", "minio")
MINIO_SECRET_KEY = os.getenv("MINIO_ROOT_PASSWORD", "minio123")
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"

BUCKETS = [
    "raw-events",
    "artifacts",
    "prompts",
    "reports"
]

def main():
    client = Minio(
        MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=MINIO_SECURE
    )

    for bucket in BUCKETS:
        try:
            if not client.bucket_exists(bucket):
                client.make_bucket(bucket)
                print(f"[OK] Created bucket: {bucket}")
            else:
                print(f"[OK] Bucket already exists: {bucket}")
        except S3Error as e:
            print(f"[ERROR] Bucket {bucket}: {e}")

if __name__ == "__main__":
    main()
