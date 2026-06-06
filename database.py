import json
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

DB_PATH = "subscriptions.db"

FREE_ANALYSES_LIMIT = 3
MAX_MEMORY_MESSAGES = 20
MAX_PERSON_HISTORY = 30


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                expires_at TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS free_uses (
                user_id INTEGER PRIMARY KEY,
                count INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS promo_activations (
                user_id INTEGER PRIMARY KEY,
                code TEXT,
                activated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memory (
                user_id INTEGER PRIMARY KEY,
                messages_json TEXT NOT NULL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS people (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                role TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS person_history (
                person_id INTEGER PRIMARY KEY,
                messages_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (person_id) REFERENCES people(id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS active_person (
                user_id INTEGER PRIMARY KEY,
                person_id INTEGER,
                FOREIGN KEY (person_id) REFERENCES people(id)
            )
        """)
        conn.commit()


# ── Subscriptions ─────────────────────────────────────────────────────────────

def check_subscription(user_id: int) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT expires_at FROM subscriptions WHERE user_id = ?", (user_id,)
        ).fetchone()
    if not row:
        return False
    return datetime.fromisoformat(row[0]) > datetime.now()


def add_subscription(user_id: int, username: str, days: int) -> datetime:
    with sqlite3.connect(DB_PATH) as conn:
        existing = conn.execute(
            "SELECT expires_at FROM subscriptions WHERE user_id = ?", (user_id,)
        ).fetchone()
        if existing and datetime.fromisoformat(existing[0]) > datetime.now():
            new_expires = datetime.fromisoformat(existing[0]) + timedelta(days=days)
        else:
            new_expires = datetime.now() + timedelta(days=days)
        conn.execute("""
            INSERT INTO subscriptions (user_id, username, expires_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                expires_at = excluded.expires_at
        """, (user_id, username, new_expires.isoformat()))
        conn.commit()
    return new_expires


def remove_subscription(user_id: int) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            "DELETE FROM subscriptions WHERE user_id = ?", (user_id,)
        )
        conn.commit()
    return cursor.rowcount > 0


def get_subscription_info(user_id: int) -> Optional[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT user_id, username, expires_at, created_at FROM subscriptions WHERE user_id = ?",
            (user_id,)
        ).fetchone()
    if not row:
        return None
    expires_at = datetime.fromisoformat(row[2])
    return {
        "user_id": row[0],
        "username": row[1],
        "expires_at": expires_at,
        "created_at": row[3],
        "is_active": expires_at > datetime.now(),
    }


def list_subscriptions() -> list:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT user_id, username, expires_at, created_at FROM subscriptions ORDER BY expires_at DESC"
        ).fetchall()
    now = datetime.now()
    return [
        {
            "user_id": r[0],
            "username": r[1],
            "expires_at": datetime.fromisoformat(r[2]),
            "created_at": r[3],
            "is_active": datetime.fromisoformat(r[2]) > now,
        }
        for r in rows
    ]


# ── Free uses ─────────────────────────────────────────────────────────────────

def get_free_uses_count(user_id: int) -> int:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT count FROM free_uses WHERE user_id = ?", (user_id,)
        ).fetchone()
    return row[0] if row else 0


def increment_free_uses(user_id: int) -> int:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT INTO free_uses (user_id, count) VALUES (?, 1)
            ON CONFLICT(user_id) DO UPDATE SET count = count + 1
        """, (user_id,))
        conn.commit()
        row = conn.execute(
            "SELECT count FROM free_uses WHERE user_id = ?", (user_id,)
        ).fetchone()
    return row[0]


# ── Promo codes ───────────────────────────────────────────────────────────────

def has_activated_promo(user_id: int) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT 1 FROM promo_activations WHERE user_id = ?", (user_id,)
        ).fetchone()
    return row is not None


def activate_promo(user_id: int, username: str, code: str) -> datetime:
    expires_at = add_subscription(user_id, username, days=36500)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT OR IGNORE INTO promo_activations (user_id, code) VALUES (?, ?)
        """, (user_id, code))
        conn.commit()
    return expires_at


# ── General memory ────────────────────────────────────────────────────────────

def get_memory(user_id: int) -> list:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT messages_json FROM memory WHERE user_id = ?", (user_id,)
        ).fetchone()
    return json.loads(row[0]) if row else []


def save_memory(user_id: int, messages: list):
    if len(messages) > MAX_MEMORY_MESSAGES:
        messages = messages[:2] + messages[-(MAX_MEMORY_MESSAGES - 2):]
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT INTO memory (user_id, messages_json, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                messages_json = excluded.messages_json,
                updated_at = excluded.updated_at
        """, (user_id, json.dumps(messages, ensure_ascii=False)))
        conn.commit()


def clear_memory(user_id: int) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute("DELETE FROM memory WHERE user_id = ?", (user_id,))
        conn.commit()
    return cursor.rowcount > 0


def has_memory(user_id: int) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT 1 FROM memory WHERE user_id = ?", (user_id,)
        ).fetchone()
    return row is not None


# ── People profiles ───────────────────────────────────────────────────────────

def add_person(user_id: int, name: str, role: str) -> int:
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            "INSERT INTO people (user_id, name, role) VALUES (?, ?, ?)",
            (user_id, name, role)
        )
        conn.commit()
    return cursor.lastrowid


def get_people(user_id: int) -> list:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT id, name, role, created_at FROM people WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,)
        ).fetchall()
    return [{"id": r[0], "name": r[1], "role": r[2], "created_at": r[3]} for r in rows]


def get_person(person_id: int) -> Optional[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT id, user_id, name, role FROM people WHERE id = ?", (person_id,)
        ).fetchone()
    if not row:
        return None
    return {"id": row[0], "user_id": row[1], "name": row[2], "role": row[3]}


def delete_person(person_id: int) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM person_history WHERE person_id = ?", (person_id,))
        conn.execute("UPDATE active_person SET person_id = NULL WHERE person_id = ?", (person_id,))
        cursor = conn.execute("DELETE FROM people WHERE id = ?", (person_id,))
        conn.commit()
    return cursor.rowcount > 0


# ── Active person ─────────────────────────────────────────────────────────────

def get_active_person(user_id: int) -> Optional[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("""
            SELECT p.id, p.name, p.role
            FROM active_person ap
            JOIN people p ON ap.person_id = p.id
            WHERE ap.user_id = ? AND ap.person_id IS NOT NULL
        """, (user_id,)).fetchone()
    if not row:
        return None
    return {"id": row[0], "name": row[1], "role": row[2]}


def set_active_person(user_id: int, person_id: int):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT INTO active_person (user_id, person_id) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET person_id = excluded.person_id
        """, (user_id, person_id))
        conn.commit()


def clear_active_person(user_id: int):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE active_person SET person_id = NULL WHERE user_id = ?", (user_id,)
        )
        conn.commit()


# ── Person history ────────────────────────────────────────────────────────────

def get_person_history(person_id: int) -> list:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT messages_json FROM person_history WHERE person_id = ?", (person_id,)
        ).fetchone()
    return json.loads(row[0]) if row else []


def save_person_history(person_id: int, messages: list):
    if len(messages) > MAX_PERSON_HISTORY:
        messages = messages[:2] + messages[-(MAX_PERSON_HISTORY - 2):]
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT INTO person_history (person_id, messages_json, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(person_id) DO UPDATE SET
                messages_json = excluded.messages_json,
                updated_at = excluded.updated_at
        """, (person_id, json.dumps(messages, ensure_ascii=False)))
        conn.commit()


def clear_person_history(person_id: int) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            "DELETE FROM person_history WHERE person_id = ?", (person_id,)
        )
        conn.commit()
    return cursor.rowcount > 0
