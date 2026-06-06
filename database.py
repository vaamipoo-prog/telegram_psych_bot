import json
import os
import psycopg2
from datetime import datetime, timedelta
from typing import Optional

FREE_ANALYSES_LIMIT = 3
MAX_MEMORY_MESSAGES = 20
MAX_PERSON_HISTORY = 30


def _get_conn():
    url = os.getenv("DATABASE_URL", "")
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return psycopg2.connect(url)


def init_db():
    conn = _get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS subscriptions (
                        user_id BIGINT PRIMARY KEY,
                        username TEXT,
                        expires_at TIMESTAMP NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS free_uses (
                        user_id BIGINT PRIMARY KEY,
                        count INTEGER DEFAULT 0
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS promo_activations (
                        user_id BIGINT PRIMARY KEY,
                        code TEXT,
                        activated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS memory (
                        user_id BIGINT PRIMARY KEY,
                        messages_json TEXT NOT NULL,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS people (
                        id SERIAL PRIMARY KEY,
                        user_id BIGINT NOT NULL,
                        name TEXT NOT NULL,
                        role TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS person_history (
                        person_id INTEGER PRIMARY KEY REFERENCES people(id),
                        messages_json TEXT NOT NULL DEFAULT '[]',
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS active_person (
                        user_id BIGINT PRIMARY KEY,
                        person_id INTEGER REFERENCES people(id)
                    )
                """)
    finally:
        conn.close()


# ── Subscriptions ─────────────────────────────────────────────────────────────

def check_subscription(user_id: int) -> bool:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT expires_at FROM subscriptions WHERE user_id = %s", (user_id,))
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return False
    return row[0] > datetime.now()


def add_subscription(user_id: int, username: str, days: int) -> datetime:
    conn = _get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT expires_at FROM subscriptions WHERE user_id = %s", (user_id,))
                existing = cur.fetchone()
                if existing and existing[0] > datetime.now():
                    new_expires = existing[0] + timedelta(days=days)
                else:
                    new_expires = datetime.now() + timedelta(days=days)
                cur.execute("""
                    INSERT INTO subscriptions (user_id, username, expires_at)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET
                        username = EXCLUDED.username,
                        expires_at = EXCLUDED.expires_at
                """, (user_id, username, new_expires))
    finally:
        conn.close()
    return new_expires


def remove_subscription(user_id: int) -> bool:
    conn = _get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM subscriptions WHERE user_id = %s", (user_id,))
                return cur.rowcount > 0
    finally:
        conn.close()


def get_subscription_info(user_id: int) -> Optional[dict]:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_id, username, expires_at, created_at FROM subscriptions WHERE user_id = %s",
                (user_id,)
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return None
    expires_at = row[2]
    return {
        "user_id": row[0],
        "username": row[1],
        "expires_at": expires_at,
        "created_at": row[3],
        "is_active": expires_at > datetime.now(),
    }


def list_subscriptions() -> list:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_id, username, expires_at, created_at FROM subscriptions ORDER BY expires_at DESC"
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    now = datetime.now()
    return [
        {
            "user_id": r[0],
            "username": r[1],
            "expires_at": r[2],
            "created_at": r[3],
            "is_active": r[2] > now,
        }
        for r in rows
    ]


# ── Free uses ─────────────────────────────────────────────────────────────────

def get_free_uses_count(user_id: int) -> int:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count FROM free_uses WHERE user_id = %s", (user_id,))
            row = cur.fetchone()
    finally:
        conn.close()
    return row[0] if row else 0


def increment_free_uses(user_id: int) -> int:
    conn = _get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO free_uses (user_id, count) VALUES (%s, 1)
                    ON CONFLICT (user_id) DO UPDATE SET count = free_uses.count + 1
                    RETURNING count
                """, (user_id,))
                return cur.fetchone()[0]
    finally:
        conn.close()


# ── Promo codes ───────────────────────────────────────────────────────────────

def has_activated_promo(user_id: int) -> bool:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM promo_activations WHERE user_id = %s", (user_id,))
            row = cur.fetchone()
    finally:
        conn.close()
    return row is not None


def activate_promo(user_id: int, username: str, code: str) -> datetime:
    expires_at = add_subscription(user_id, username, days=36500)
    conn = _get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO promo_activations (user_id, code) VALUES (%s, %s)
                    ON CONFLICT (user_id) DO NOTHING
                """, (user_id, code))
    finally:
        conn.close()
    return expires_at


# ── General memory ────────────────────────────────────────────────────────────

def get_memory(user_id: int) -> list:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT messages_json FROM memory WHERE user_id = %s", (user_id,))
            row = cur.fetchone()
    finally:
        conn.close()
    return json.loads(row[0]) if row else []


def save_memory(user_id: int, messages: list):
    if len(messages) > MAX_MEMORY_MESSAGES:
        messages = messages[:2] + messages[-(MAX_MEMORY_MESSAGES - 2):]
    conn = _get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO memory (user_id, messages_json, updated_at)
                    VALUES (%s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (user_id) DO UPDATE SET
                        messages_json = EXCLUDED.messages_json,
                        updated_at = EXCLUDED.updated_at
                """, (user_id, json.dumps(messages, ensure_ascii=False)))
    finally:
        conn.close()


def clear_memory(user_id: int) -> bool:
    conn = _get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM memory WHERE user_id = %s", (user_id,))
                return cur.rowcount > 0
    finally:
        conn.close()


def has_memory(user_id: int) -> bool:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM memory WHERE user_id = %s", (user_id,))
            row = cur.fetchone()
    finally:
        conn.close()
    return row is not None


# ── People profiles ───────────────────────────────────────────────────────────

def add_person(user_id: int, name: str, role: str) -> int:
    conn = _get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO people (user_id, name, role) VALUES (%s, %s, %s) RETURNING id",
                    (user_id, name, role)
                )
                return cur.fetchone()[0]
    finally:
        conn.close()


def get_people(user_id: int) -> list:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, role, created_at FROM people WHERE user_id = %s ORDER BY created_at DESC",
                (user_id,)
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    return [{"id": r[0], "name": r[1], "role": r[2], "created_at": r[3]} for r in rows]


def get_person(person_id: int) -> Optional[dict]:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, user_id, name, role FROM people WHERE id = %s", (person_id,)
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return {"id": row[0], "user_id": row[1], "name": row[2], "role": row[3]}


def delete_person(person_id: int) -> bool:
    conn = _get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM person_history WHERE person_id = %s", (person_id,))
                cur.execute("UPDATE active_person SET person_id = NULL WHERE person_id = %s", (person_id,))
                cur.execute("DELETE FROM people WHERE id = %s", (person_id,))
                return cur.rowcount > 0
    finally:
        conn.close()


# ── Active person ─────────────────────────────────────────────────────────────

def get_active_person(user_id: int) -> Optional[dict]:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT p.id, p.name, p.role
                FROM active_person ap
                JOIN people p ON ap.person_id = p.id
                WHERE ap.user_id = %s AND ap.person_id IS NOT NULL
            """, (user_id,))
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return {"id": row[0], "name": row[1], "role": row[2]}


def set_active_person(user_id: int, person_id: int):
    conn = _get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO active_person (user_id, person_id) VALUES (%s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET person_id = EXCLUDED.person_id
                """, (user_id, person_id))
    finally:
        conn.close()


def clear_active_person(user_id: int):
    conn = _get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE active_person SET person_id = NULL WHERE user_id = %s", (user_id,)
                )
    finally:
        conn.close()


# ── Person history ────────────────────────────────────────────────────────────

def get_person_history(person_id: int) -> list:
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT messages_json FROM person_history WHERE person_id = %s", (person_id,)
            )
            row = cur.fetchone()
    finally:
        conn.close()
    return json.loads(row[0]) if row else []


def save_person_history(person_id: int, messages: list):
    if len(messages) > MAX_PERSON_HISTORY:
        messages = messages[:2] + messages[-(MAX_PERSON_HISTORY - 2):]
    conn = _get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO person_history (person_id, messages_json, updated_at)
                    VALUES (%s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (person_id) DO UPDATE SET
                        messages_json = EXCLUDED.messages_json,
                        updated_at = EXCLUDED.updated_at
                """, (person_id, json.dumps(messages, ensure_ascii=False)))
    finally:
        conn.close()


def clear_person_history(person_id: int) -> bool:
    conn = _get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM person_history WHERE person_id = %s", (person_id,))
                return cur.rowcount > 0
    finally:
        conn.close()
