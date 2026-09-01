"""SQLite storage: two tables.

products  - one row per (distributor, pid): the current known facts about a
            listing (title, url, category...). Upserted every run.
snapshots - one row per (distributor, pid, snapshot_date): the daily
            observation (price, stock, rating...). This is what trend charts
            are built from.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "apollo_dashboard.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    distributor   TEXT NOT NULL,
    pid           TEXT NOT NULL,
    title         TEXT,
    brand         TEXT,
    url           TEXT,
    category_path TEXT,
    first_seen    TEXT,
    last_seen     TEXT,
    PRIMARY KEY (distributor, pid)
);

CREATE TABLE IF NOT EXISTS snapshots (
    distributor     TEXT NOT NULL,
    pid             TEXT NOT NULL,
    snapshot_date   TEXT NOT NULL,
    scraped_at      TEXT NOT NULL,
    price           REAL,
    sale_price      REAL,
    discount_pct    REAL,
    in_stock        INTEGER,
    availability_raw TEXT,
    rating          REAL,
    review_count    INTEGER,
    merch_badges    TEXT,
    PRIMARY KEY (distributor, pid, snapshot_date)
);

CREATE TABLE IF NOT EXISTS run_log (
    run_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at     TEXT,
    finished_at    TEXT,
    distributor    TEXT,
    catalog_count  INTEGER,
    enriched_count INTEGER,
    error          TEXT
);
"""


def encode_badges(badges) -> str | None:
    """merch_badges is tri-state (see ProductRecord): None means "not observed
    this run" and must stay NULL; an empty list means "observed, no badges" and
    is stored as '' so it's distinguishable from NULL in SQL."""
    if badges is None:
        return None
    return ",".join(sorted(badges))


def decode_badges(value) -> list[str] | None:
    if value is None:
        return None
    return [b for b in value.split(",") if b]


@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# Columns added after the first release. CREATE TABLE IF NOT EXISTS won't add
# a column to a table that already exists, so new ones go here too.
MIGRATIONS = [
    ("snapshots", "merch_badges", "TEXT"),
]


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        for table, column, coltype in MIGRATIONS:
            existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def upsert_products_and_snapshot(records, snapshot_date: str | None = None) -> None:
    snapshot_date = snapshot_date or datetime.now().date().isoformat()
    scraped_at = datetime.now(timezone.utc).isoformat()

    with connect() as conn:
        for r in records:
            conn.execute(
                """
                INSERT INTO products (distributor, pid, title, brand, url, category_path, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(distributor, pid) DO UPDATE SET
                    title=excluded.title, brand=excluded.brand, url=excluded.url,
                    category_path=excluded.category_path, last_seen=excluded.last_seen
                """,
                (r.distributor, r.pid, r.title, r.brand, r.url, r.category_path, snapshot_date, snapshot_date),
            )

            discount_pct = None
            if r.price and r.sale_price is not None and r.price > 0:
                discount_pct = round((r.price - r.sale_price) / r.price * 100, 2)

            conn.execute(
                """
                INSERT INTO snapshots (distributor, pid, snapshot_date, scraped_at, price, sale_price,
                                        discount_pct, in_stock, availability_raw, rating, review_count,
                                        merch_badges)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(distributor, pid, snapshot_date) DO UPDATE SET
                    scraped_at=excluded.scraped_at, price=excluded.price, sale_price=excluded.sale_price,
                    discount_pct=excluded.discount_pct, in_stock=excluded.in_stock,
                    availability_raw=excluded.availability_raw, rating=excluded.rating,
                    review_count=excluded.review_count,
                    merch_badges=COALESCE(excluded.merch_badges, snapshots.merch_badges)
                """,
                (
                    r.distributor, r.pid, snapshot_date, scraped_at, r.price, r.sale_price, discount_pct,
                    None if r.in_stock is None else int(r.in_stock), r.availability_raw, r.rating, r.review_count,
                    encode_badges(r.merch_badges),
                ),
            )


def log_run(started_at, finished_at, distributor, catalog_count, enriched_count, error=None) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO run_log (started_at, finished_at, distributor, catalog_count, enriched_count, error)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (started_at, finished_at, distributor, catalog_count, enriched_count, error),
        )
