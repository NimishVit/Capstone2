"""Create a consistent SQLite snapshot, including committed WAL transactions."""
import argparse
import sqlite3
from pathlib import Path

def backup(source, destination):
    source, destination = Path(source), Path(destination)
    if not source.is_file(): raise ValueError('Source database does not exist')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('xb'): pass
    try:
        with sqlite3.connect(source.resolve().as_uri()+'?mode=ro', uri=True) as src:
            with sqlite3.connect(destination) as dst:
                src.backup(dst)
                if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('Backup integrity check failed')
    except Exception:
        destination.unlink(missing_ok=True)
        raise

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source'); parser.add_argument('destination')
    args = parser.parse_args()
    backup(args.source, args.destination)
    print('Verified database snapshot created:', args.destination)
