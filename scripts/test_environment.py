import os
import sys
import json
import requests
import psycopg2
from minio import Minio

POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_DB = os.getenv("POSTGRES_DB", "resilience")
POSTGRES_USER = os.getenv("POSTGRES_USER", "resilience")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "resilience")

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ROOT_USER", "minio")
MINIO_SECRET_KEY = os.getenv("MINIO_ROOT_PASSWORD", "minio123")
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:1234")
LLM_CHAT_PATH = os.getenv("LLM_CHAT_PATH", "/v1/chat/completions")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen2.5-7b-instruct")


def test_postgres():
    print("Testing PostgreSQL...")
    conn = psycopg2.connect(
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        dbname=POSTGRES_DB,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
    )
    with conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database(), current_user, now();")
            row = cur.fetchone()
            print(f"[OK] PostgreSQL connected: db={row[0]}, user={row[1]}, time={row[2]}")
    conn.close()


def test_minio():
    print("Testing MinIO...")
    client = Minio(
        MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=MINIO_SECURE,
    )
    buckets = client.list_buckets()
    print("[OK] MinIO connected. Buckets:")
    for b in buckets:
        print(f" - {b.name}")


def test_lmstudio():
    print("Testing LM Studio...")
    url = f"{LLM_BASE_URL.rstrip('/')}{LLM_CHAT_PATH}"
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": "You are a JSON-only assistant."},
            {"role": "user", "content": "Return exactly this JSON: {\"status\":\"ok\"}"}
        ],
        "temperature": 0.1,
        "stream": False,
        "response_format": {"type": "json_object"}
    }
    r = requests.post(url, json=payload, timeout=60)
    r.raise_for_status()
    data = r.json()
    print("[OK] LM Studio connected.")
    print(json.dumps(data, indent=2)[:1200])


def main():
    failures = 0

    for fn in [test_postgres, test_minio, test_lmstudio]:
        try:
            fn()
        except Exception as e:
            failures += 1
            print(f"[FAIL] {fn.__name__}: {e}")

    if failures:
        print(f"\nEnvironment check finished with {failures} failure(s).")
        sys.exit(1)

    print("\nEnvironment check passed.")


if __name__ == "__main__":
    main()
    