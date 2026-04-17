import json
from pathlib import Path

import httpx

BASE_DIR = Path(__file__).resolve().parents[1]
SAMPLE_DIR = BASE_DIR / 'sample_data'
FILES = sorted([p for p in SAMPLE_DIR.rglob('*.json') if p.is_file()])

INGEST_BASE = 'http://localhost:8000'


def main() -> None:
    with httpx.Client(timeout=60) as client:
        for path in FILES:
            content = path.read_bytes()
            payload = json.loads(content)
            folder = path.parent.name

            if folder == 'jira':
                response = client.post(
                    f'{INGEST_BASE}/adapters/jira/webhook',
                    json=payload,
                )
            elif folder == 'servicenow':
                response = client.post(
                    f'{INGEST_BASE}/adapters/servicenow/event',
                    json=payload,
                )
            else:
                response = client.post(
                    f'{INGEST_BASE}/ingest/file',
                    files={'file': (path.name, content, 'application/json')},
                )

            response.raise_for_status()
            print(f'{path.relative_to(BASE_DIR)} -> {response.json()}')


if __name__ == '__main__':
    main()
