"""Start (or reuse) a throwaway local PostgreSQL with pgvector for integration tests.

Run with a Python 3.9-3.12 interpreter that has `pgserver` installed (pgserver publishes no 3.13 wheels):

    python scripts/dev_postgres.py            # prints a connection URL; the server keeps running
    python scripts/dev_postgres.py --stop     # stops it

Then, in another terminal:

    $env:CRM_TEST_DATABASE_URL = "<printed url>"
    python -m pytest -q
"""
import argparse
import os
import sys
import tempfile
from pathlib import Path

import pgserver

DATA_DIR = Path(os.environ.get("CRM_DEV_PG_DIR", Path(tempfile.gettempdir()) / "synthra-pgdata"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stop", action="store_true")
    args = parser.parse_args()
    server = pgserver.get_server(DATA_DIR, cleanup_mode="stop" if args.stop else None)
    if args.stop:
        server.cleanup()
        print("stopped")
        return
    print(server.get_uri())
    has_vector = server.psql("select count(*) from pg_available_extensions where name = 'vector';")
    print("pgvector available:", "1" in str(has_vector).split())
    sys.stdout.flush()


if __name__ == "__main__":
    main()
