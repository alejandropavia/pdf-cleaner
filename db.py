import sqlite3
from pathlib import Path
from datetime import date

DB_PATH = Path("app.db")


def _connect():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    con = _connect()
    cur = con.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS tokens (
            token TEXT PRIMARY KEY,
            plan TEXT NOT NULL,
            email TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS monthly_usage (
            key_type TEXT NOT NULL,
            key_value TEXT NOT NULL,
            month TEXT NOT NULL,
            used INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (key_type, key_value, month)
        )
        """
    )

    con.commit()
    con.close()


def save_token(token: str, plan: str, email: str):
    con = _connect()
    con.execute(
        "INSERT OR REPLACE INTO tokens(token, plan, email, created_at) VALUES(?,?,?,?)",
        (token, plan, email or "", date.today().isoformat()),
    )
    con.commit()
    con.close()


def get_token(token: str):
    con = _connect()
    row = con.execute("SELECT token, plan, email FROM tokens WHERE token=?", (token,)).fetchone()
    con.close()
    return dict(row) if row else None


def get_used(key_type: str, key_value: str, month: str) -> int:
    con = _connect()
    row = con.execute(
        "SELECT used FROM monthly_usage WHERE key_type=? AND key_value=? AND month=?",
        (key_type, key_value, month),
    ).fetchone()
    con.close()
    return int(row["used"]) if row else 0


def inc_used(key_type: str, key_value: str, month: str):
    con = _connect()
    cur = con.cursor()
    cur.execute(
        """
        INSERT INTO monthly_usage(key_type, key_value, month, used)
        VALUES(?,?,?,1)
        ON CONFLICT(key_type, key_value, month)
        DO UPDATE SET used = used + 1
        """,
        (key_type, key_value, month),
    )
    con.commit()
    con.close()
