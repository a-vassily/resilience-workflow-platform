from pathlib import Path

import httpx

BASE_DIR = Path(__file__).resolve().parents[1]
SAMPLE_DIR = BASE_DIR / 'sample_data'
FILES = sorted([p for p in SAMPLE_DIR.rglob('*.json') if p.is_file()])


def main() -> None:
    with httpx.Client(timeout=60) as client:
        for path in FILES:
            with path.open('rb') as fh:
                response = client.post('http://localhost:8000/ingest/file', files={'file': (path.name, fh, 'application/json')})
                response.raise_for_status()
                print(f'{path.relative_to(BASE_DIR)} -> {response.json()}')


if __name__ == '__main__':
    main()
