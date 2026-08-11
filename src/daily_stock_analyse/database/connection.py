from __future__ import annotations

from contextlib import contextmanager

@contextmanager
def postgres_connection(database_url: str):
    import psycopg
    from psycopg.rows import dict_row

    conn = psycopg.connect(database_url, row_factory=dict_row)
    try:
        yield conn
    finally:
        conn.close()
