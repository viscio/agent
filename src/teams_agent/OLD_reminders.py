import os
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiohttp.web import Application

from microsoft_agents.hosting.core import AgentApplication, TurnContext
from microsoft_agents.hosting.aiohttp import CloudAdapter

# We rely on Bot Framework helper for conversation references
from botbuilder.core import TurnContext as BfTurnContext

DB_PATH = os.getenv("REMINDERS_DB", "reminders.db")
LOCAL_TZ = os.getenv("LOCAL_TZ", "UTC")
APP_ID = os.getenv("MICROSOFT_APP_ID", "")  # empty in local playground


@dataclass
class Reminder:
    id: int
    due_at_utc: datetime
    text: str
    conversation_ref: Dict[str, Any]
    sent: int


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _connect() as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS reminders (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              due_at_utc TEXT NOT NULL,
              text TEXT NOT NULL,
              conversation_ref TEXT NOT NULL,
              sent INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        con.commit()


def save_reminder(due_in_minutes: int, text: str, context: TurnContext) -> Tuple[int, datetime]:
    """Persist reminder and return (id, due_at_utc)."""
    due_at = datetime.now(timezone.utc) + timedelta(minutes=int(due_in_minutes))
    # Extract conversation reference compatible with Bot Framework
    conv_ref = BfTurnContext.get_conversation_reference(context.activity)
    conv_ref_json = json.dumps(conv_ref.serialize()) if hasattr(conv_ref, "serialize") else json.dumps(conv_ref.__dict__)

    with _connect() as con:
        cur = con.execute(
            "INSERT INTO reminders (due_at_utc, text, conversation_ref, sent) VALUES (?, ?, ?, 0)",
            (due_at.isoformat(), text, conv_ref_json),
        )
        con.commit()
        return cur.lastrowid, due_at


def _row_to_reminder(row: sqlite3.Row) -> Reminder:
    return Reminder(
        id=row["id"],
        due_at_utc=datetime.fromisoformat(row["due_at_utc"]),
        text=row["text"],
        conversation_ref=json.loads(row["conversation_ref"]),
        sent=row["sent"],
    )


def fetch_due(now: datetime) -> List[Reminder]:
    with _connect() as con:
        rows = con.execute(
            "SELECT * FROM reminders WHERE sent=0 AND due_at_utc <= ? ORDER BY due_at_utc ASC",
            (now.isoformat(),),
        ).fetchall()
    return [_row_to_reminder(r) for r in rows]


def mark_sent(reminder_id: int):
    with _connect() as con:
        con.execute("UPDATE reminders SET sent=1 WHERE id=?", (reminder_id,))
        con.commit()


async def _send_proactive(adapter: CloudAdapter, conv_ref: Dict[str, Any], message: str):
    async def _callback(tc):
        await tc.send_activity(message)

    # In local Playground, APP_ID can be empty.
    await adapter.continue_conversation(conv_ref, _callback, APP_ID)


def setup_scheduler(app: Application, agent_app: AgentApplication):
    init_db()

    scheduler = AsyncIOScheduler(timezone="UTC")

    async def tick():
        now = datetime.now(timezone.utc)
        due = fetch_due(now)
        for r in due:
            try:
                await _send_proactive(agent_app.adapter, r.conversation_ref, f"⏰ Reminder: {r.text}")
                mark_sent(r.id)
            except Exception as e:  # pragma: no cover
                # In production, log this!
                print(f"Failed to send reminder {r.id}: {e}")

    # Run every 10 seconds (fine for demo)
    #scheduler.add_job(lambda: agent_app.adapter.loop.create_task(tick()), "interval", seconds=10)
    scheduler.add_job(tick, "interval", seconds=10, coalesce=True, max_instances=1)

    async def _on_startup(_):
        scheduler.start()
        print("Reminder scheduler started")

    async def _on_cleanup(_):
        scheduler.shutdown(wait=False)

    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
