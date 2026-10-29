"""Stage 4 of the pipeline: keep every snapshot in SQLite, export flat files.

Stats like monthly listeners and play counts move daily, so rows are keyed
by (id, snapshot date) and a rerun on the same day overwrites rather than
duplicates. Standard library only.
"""

import csv
import json
import sqlite3
from dataclasses import asdict
from datetime import date

SCHEMA = """
CREATE TABLE IF NOT EXISTS entities (
    spotify_id TEXT, snapshot TEXT, kind TEXT, name TEXT, subtitle TEXT,
    release_date TEXT, image TEXT, track_count_seen INTEGER,
    track_count_total INTEGER, maybe_truncated INTEGER, followers INTEGER,
    monthly_listeners INTEGER, world_rank INTEGER, top_cities TEXT,
    description TEXT,
    PRIMARY KEY (spotify_id, snapshot)
);
CREATE TABLE IF NOT EXISTS tracks (
    parent_id TEXT, snapshot TEXT, position INTEGER, track_id TEXT,
    name TEXT, artists TEXT, duration_s REAL, explicit INTEGER,
    playcount INTEGER, bpm REAL, camelot_key TEXT,
    PRIMARY KEY (parent_id, snapshot, position)
);
"""


class Store:
    def __init__(self, path="spotify.db"):
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)
        self.snapshot = date.today().isoformat()

    def save(self, entity, tracks):
        e = asdict(entity)
        e["top_cities"] = json.dumps(e["top_cities"])
        e["snapshot"] = self.snapshot
        cols = ", ".join(e)
        self.db.execute(
            f"INSERT OR REPLACE INTO entities ({cols}) VALUES ({', '.join('?' * len(e))})",
            list(e.values()),
        )
        self.db.execute("DELETE FROM tracks WHERE parent_id=? AND snapshot=?",
                        (entity.spotify_id, self.snapshot))
        for t in tracks:
            row = asdict(t)
            row["snapshot"] = self.snapshot
            self.db.execute(
                f"INSERT OR REPLACE INTO tracks ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                list(row.values()),
            )
        self.db.commit()

    def export_csv(self, table, path):
        cur = self.db.execute(f"SELECT * FROM {table} WHERE snapshot=?", (self.snapshot,))
        cols = [c[0] for c in cur.description]
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(cols)
            w.writerows(cur)
        return path

    def export_jsonl(self, table, path):
        cur = self.db.execute(f"SELECT * FROM {table} WHERE snapshot=?", (self.snapshot,))
        cols = [c[0] for c in cur.description]
        with open(path, "w", encoding="utf-8") as f:
            for row in cur:
                f.write(json.dumps(dict(zip(cols, row)), ensure_ascii=False) + "\n")
        return path

    def history(self, spotify_id, column="monthly_listeners"):
        """Time series of one stat, for the trend checks this pipeline exists for."""
        assert column in {"monthly_listeners", "followers", "world_rank", "track_count_total"}
        return self.db.execute(
            f"SELECT snapshot, {column} FROM entities WHERE spotify_id=? ORDER BY snapshot",
            (spotify_id,),
        ).fetchall()
