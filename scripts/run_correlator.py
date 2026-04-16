from app.correlator_worker.main import run_once

if __name__ == '__main__':
    count = run_once()
    print(f'Created {count} candidate incidents.')
