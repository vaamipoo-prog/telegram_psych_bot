import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Optional

try:
    import psycopg2
except ImportError:
    psycopg2 = None

DB_PATH = "subscriptions.db"
FREE_ANALYSES_LIMIT = 3
MAX_MEMORY_MESSAGES = 20
MAX_PERSON_HISTORY = 30

USE_POSTGRES = bool(os.getenv("DATABASE_URL")) and psycopg2 is not None
PH = "%s" if USE_POSTGRES else "?"


def _get_conn():
    if USE_POSTGRES:
        url = os.getenv("DATABASE_URL")
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        return psycopg2.connect(url, sslmode="require")
    return sqlite3.connect(DB_PATH)


@contextmanager
def _cursor():
    conn = _get_conn()
    cur = conn.cursor()
    try:
        yield cur
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        cur.close()
        conn.close()


def _dt(val) -> datetime:
    """Normalize to datetime: psycopg2 returns datetime objects, sqlite3 returns strings."""
    if isinstance(val, datetime):
        return val
    return datetime.fromisoformat(str(val))


def init_db():
    if USE_POSTGRES:
        _id = "SERIAL PRIMARY KEY"
        _int = "BIGINT"
        _ts = "TIMESTAMP"
        _fk_people = "REFERENCES people(id)"
    else:
        _id = "INTEGER PRIMARY KEY"
        _int = "INTEGER"
        _ts = "TEXT"
        _fk_people = ""

    with _cursor() as cur:
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS subscriptions (
                user_id {_int} PRIMARY KEY,
                username TEXT,
                expires_at {_ts} NOT NULL,
                created_at {_ts} DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS free_uses (
                user_id {_int} PRIMARY KEY,
                count INTEGER DEFAULT 0
            )
        """)
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS promo_activations (
                user_id {_int} PRIMARY KEY,
                code TEXT,
                activated_at {_ts} DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS memory (
                user_id {_int} PRIMARY KEY,
                messages_json TEXT NOT NULL,
                updated_at {_ts} DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS people (
                id {_id},
                user_id {_int} NOT NULL,
                name TEXT NOT NULL,
                role TEXT NOT NULL,
                created_at {_ts} DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS person_history (
                person_id INTEGER PRIMARY KEY {_fk_people},
                messages_json TEXT NOT NULL DEFAULT '[]',
                updated_at {_ts} DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS active_person (
                user_id {_int} PRIMARY KEY,
                person_id INTEGER {_fk_people}
            )
        """)


# ── Subscriptions ─────────────────────────────────────────────────────────────

def check_subscription(user_id: int) -> bool:
    with _cursor() as cur:
        cur.execute(f"SELECT expires_at FROM subscriptions WHERE user_id = {PH}", (user_id,))
        row = cur.fetchone()
    if not row:
        return False
    return _dt(row[0]) > datetime.now()


def add_subscription(user_id: int, username: str, days: int) -> datetime:
    with _cursor() as cur:
        cur.execute(f"SELECT expires_at FROM subscriptions WHERE user_id = {PH}", (user_id,))
        existing = cur.fetchone()
        if existing and _dt(existing[0]) > datetime.now():
            new_expires = _dt(existing[0]) + timedelta(days=days)
        else:
            new_expires = datetime.now() + timedelta(days=days)
        cur.execute(f"""
            INSERT INTO subscriptions (user_id, username, expires_at)
            VALUES ({PH}, {PH}, {PH})
            ON CONFLICT (user_id) DO UPDATE SET
                username = EXCLUDED.username,
                expires_at = EXCLUDED.expires_at
        """, (user_id, username, new_expires if USE_POSTGRES else new_expires.isoformat()))
    return new_expires


def remove_subscription(user_id: int) -> bool:
    with _cursor() as cur:
        cur.execute(f"DELETE FROM subscriptions WHERE user_id = {PH}", (user_id,))
        return cur.rowcount > 0


def get_subscription_info(user_id: int) -> Optional[dict]:
    with _cursor() as cur:
        cur.execute(
            f"SELECT user_id, username, expires_at, created_at FROM subscriptions WHERE user_id = {PH}",
            (user_id,)
        )
        row = cur.fetchone()
    if not row:
        return None
    expires_at = _dt(row[2])
    return {
        "user_id": row[0],
        "username": row[1],
        "expires_at": expires_at,
        "created_at": row[3],
        "is_active": expires_at > datetime.now(),
    }


def list_subscriptions() -> list:
    with _cursor() as cur:
        cur.execute(
            "SELECT user_id, username, expires_at, created_at FROM subscriptions ORDER BY expires_at DESC"
        )
        rows = cur.fetchall()
    now = datetime.now()
    return [
        {
            "user_id": r[0],
            "username": r[1],
            "expires_at": _dt(r[2]),
            "created_at": r[3],
            "is_active": _dt(r[2]) > now,
        }
        for r in rows
    ]


# ── Free uses ─────────────────────────────────────────────────────────────────

def get_free_uses_count(user_id: int) -> int:
    with _cursor() as cur:
        cur.execute(f"SELECT count FROM free_uses WHERE user_id = {PH}", (user_id,))
        row = cur.fetchone()
    return row[0] if row else 0


def increment_free_uses(user_id: int) -> int:
    with _cursor() as cur:
        if USE_POSTGRES:
            cur.execute(f"""
                INSERT INTO free_uses (user_id, count) VALUES ({PH}, 1)
                ON CONFLICT (user_id) DO UPDATE SET count = free_uses.count + 1
                RETURNING count
            """, (user_id,))
            return cur.fetchone()[0]
        else:
            cur.execute(f"""
                INSERT INTO free_uses (user_id, count) VALUES ({PH}, 1)
                ON CONFLICT (user_id) DO UPDATE SET count = count + 1
            """, (user_id,))
            cur.execute(f"SELECT count FROM free_uses WHERE user_id = {PH}", (user_id,))
            return cur.fetchone()[0]


# ── Promo codes ───────────────────────────────────────────────────────────────

def has_activated_promo(user_id: int) -> bool:
    with _cursor() as cur:
        cur.execute(f"SELECT 1 FROM promo_activations WHERE user_id = {PH}", (user_id,))
        row = cur.fetchone()
    return row is not None


def activate_promo(user_id: int, username: str, code: str) -> datetime:
    expires_at = add_subscription(user_id, username, days=36500)
    with _cursor() as cur:
        cur.execute(f"""
            INSERT INTO promo_activations (user_id, code) VALUES ({PH}, {PH})
            ON CONFLICT (user_id) DO NOTHING
        """, (user_id, code))
    return expires_at


# ── General memory ────────────────────────────────────────────────────────────

def get_memory(user_id: int) -> list:
    with _cursor() as cur:
        cur.execute(f"SELECT messages_json FROM memory WHERE user_id = {PH}", (user_id,))
        row = cur.fetchone()
    return json.loads(row[0]) if row else []


def save_memory(user_id: int, messages: list):
    if len(messages) > MAX_MEMORY_MESSAGES:
        messages = messages[:2] + messages[-(MAX_MEMORY_MESSAGES - 2):]
    with _cursor() as cur:
        cur.execute(f"""
            INSERT INTO memory (user_id, messages_json, updated_at)
            VALUES ({PH}, {PH}, CURRENT_TIMESTAMP)
            ON CONFLICT (user_id) DO UPDATE SET
                messages_json = EXCLUDED.messages_json,
                updated_at = EXCLUDED.updated_at
        """, (user_id, json.dumps(messages, ensure_ascii=False)))


def clear_memory(user_id: int) -> bool:
    with _cursor() as cur:
        cur.execute(f"DELETE FROM memory WHERE user_id = {PH}", (user_id,))
        return cur.rowcount > 0


def has_memory(user_id: int) -> bool:
    with _cursor() as cur:
        cur.execute(f"SELECT 1 FROM memory WHERE user_id = {PH}", (user_id,))
        row = cur.fetchone()
    return row is not None


# ── People profiles ───────────────────────────────────────────────────────────

def add_person(user_id: int, name: str, role: str) -> int:
    with _cursor() as cur:
        if USE_POSTGRES:
            cur.execute(
                f"INSERT INTO people (user_id, name, role) VALUES ({PH}, {PH}, {PH}) RETURNING id",
                (user_id, name, role)
            )
            return cur.fetchone()[0]
        else:
            cur.execute(
                f"INSERT INTO people (user_id, name, role) VALUES ({PH}, {PH}, {PH})",
                (user_id, name, role)
            )
            return cur.lastrowid


def get_people(user_id: int) -> list:
    with _cursor() as cur:
        cur.execute(
            f"SELECT id, name, role, created_at FROM people WHERE user_id = {PH} ORDER BY created_at DESC",
            (user_id,)
        )
        rows = cur.fetchall()
    return [{"id": r[0], "name": r[1], "role": r[2], "created_at": r[3]} for r in rows]


def get_person(person_id: int) -> Optional[dict]:
    with _cursor() as cur:
        cur.execute(
            f"SELECT id, user_id, name, role FROM people WHERE id = {PH}", (person_id,)
        )
        row = cur.fetchone()
    if not row:
        return None
    return {"id": row[0], "user_id": row[1], "name": row[2], "role": row[3]}


def delete_person(person_id: int) -> bool:
    with _cursor() as cur:
        cur.execute(f"DELETE FROM person_history WHERE person_id = {PH}", (person_id,))
        cur.execute(f"UPDATE active_person SET person_id = NULL WHERE person_id = {PH}", (person_id,))
        cur.execute(f"DELETE FROM people WHERE id = {PH}", (person_id,))
        return cur.rowcount > 0


# ── Active person ─────────────────────────────────────────────────────────────

def get_active_person(user_id: int) -> Optional[dict]:
    with _cursor() as cur:
        cur.execute(f"""
            SELECT p.id, p.name, p.role
            FROM active_person ap
            JOIN people p ON ap.person_id = p.id
            WHERE ap.user_id = {PH} AND ap.person_id IS NOT NULL
        """, (user_id,))
        row = cur.fetchone()
    if not row:
        return None
    return {"id": row[0], "name": row[1], "role": row[2]}


def set_active_person(user_id: int, person_id: int):
    with _cursor() as cur:
        cur.execute(f"""
            INSERT INTO active_person (user_id, person_id) VALUES ({PH}, {PH})
            ON CONFLICT (user_id) DO UPDATE SET person_id = EXCLUDED.person_id
        """, (user_id, person_id))


def clear_active_person(user_id: int):
    with _cursor() as cur:
        cur.execute(
            f"UPDATE active_person SET person_id = NULL WHERE user_id = {PH}", (user_id,)
        )


# ── Person history ────────────────────────────────────────────────────────────

def get_person_history(person_id: int) -> list:
    with _cursor() as cur:
        cur.execute(
            f"SELECT messages_json FROM person_history WHERE person_id = {PH}", (person_id,)
        )
        row = cur.fetchone()
    return json.loads(row[0]) if row else []


def save_person_history(person_id: int, messages: list):
    if len(messages) > MAX_PERSON_HISTORY:
        messages = messages[:2] + messages[-(MAX_PERSON_HISTORY - 2):]
    with _cursor() as cur:
        cur.execute(f"""
            INSERT INTO person_history (person_id, messages_json, updated_at)
            VALUES ({PH}, {PH}, CURRENT_TIMESTAMP)
            ON CONFLICT (person_id) DO UPDATE SET
                messages_json = EXCLUDED.messages_json,
                updated_at = EXCLUDED.updated_at
        """, (person_id, json.dumps(messages, ensure_ascii=False)))


def clear_person_history(person_id: int) -> bool:
    with _cursor() as cur:
        cur.execute(f"DELETE FROM person_history WHERE person_id = {PH}", (person_id,))
        return cur.rowcount > 0
