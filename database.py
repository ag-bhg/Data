import sqlite3
from pathlib import Path

DB_PATH = "/tmp/data.db"

def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tanggal TEXT NOT NULL,
                periode TEXT NOT NULL,
                nomor TEXT NOT NULL,
                source_url TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(tanggal, periode, nomor)
            )
        """)
        conn.commit()

def upsert_rows(rows):
    changed = 0
    with connect() as conn:
        for row in rows:
            cur = conn.execute("""
                INSERT INTO history (tanggal, periode, nomor, source_url)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(tanggal, periode, nomor)
                DO UPDATE SET
                    source_url=excluded.source_url,
                    updated_at=CURRENT_TIMESTAMP
            """, (row["tanggal"], row["periode"], row["nomor"], row.get("source_url")))
            changed += cur.rowcount
        conn.commit()
    return changed

def get_rows(page, per_page):
    offset = (page - 1) * per_page
    with connect() as conn:
        return conn.execute("""
            SELECT tanggal, periode, nomor, source_url
            FROM history
            ORDER BY id DESC
            LIMIT ? OFFSET ?
        """, (per_page, offset)).fetchall()

def count_rows():
    with connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM history").fetchone()[0]
