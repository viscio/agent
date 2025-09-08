import os
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple

from aiohttp.web import Application
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from microsoft_agents.hosting.core import AgentApplication, TurnContext
from microsoft_agents.hosting.aiohttp import CloudAdapter

from botbuilder.core import TurnContext as BfTurnContext
from botbuilder.schema import ConversationReference


# ---- Configuration ----
DB_PATH = os.getenv("REMINDERS_DB", "reminders.db")
APP_ID = os.getenv("MICROSOFT_APP_ID", "")  # In local playground this can be empty.


# ---- Data model ----
@dataclass
class Reminder:
    id: int
    due_at_utc: datetime
    text: str
    conversation_ref: Dict[str, Any]
    sent: int


# ---- SQLite helpers ----
def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
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


def mark_sent(reminder_id: int) -> None:
    with _connect() as con:
        con.execute("UPDATE reminders SET sent=1 WHERE id=?", (reminder_id,))
        con.commit()


# ---- ConversationReference (de)serialization ----
def _conv_ref_to_dict(conv_ref: ConversationReference) -> Dict[str, Any]:
    """
    Convert a ConversationReference into a plain dict (pydantic-safe).
    Works with both pydantic v1 and v2 models.
    """
    if hasattr(conv_ref, "model_dump"):
        # pydantic v2
        return conv_ref.model_dump(exclude_none=True)  # type: ignore[attr-defined]
    if hasattr(conv_ref, "dict"):
        # pydantic v1
        return conv_ref.dict(exclude_none=True)  # type: ignore[attr-defined]
    # Fallback (very defensive, should rarely be needed)
    return {
        "bot": {
            "id": getattr(getattr(conv_ref, "bot", None), "id", None),
            "name": getattr(getattr(conv_ref, "bot", None), "name", None),
        },
        "user": {
            "id": getattr(getattr(conv_ref, "user", None), "id", None),
            "name": getattr(getattr(conv_ref, "user", None), "name", None),
        },
        "conversation": {
            "id": getattr(getattr(conv_ref, "conversation", None), "id", None),
        },
        "channel_id": getattr(conv_ref, "channel_id", None),
        "service_url": getattr(conv_ref, "service_url", None),
        "locale": getattr(conv_ref, "locale", None),
    }


def _dict_to_conv_ref(data: Dict[str, Any]) -> ConversationReference:
    """Rebuild a ConversationReference from a plain dict."""
    return ConversationReference(**data)


# ---- Public API ----
def save_reminder(due_in_minutes: int, text: str, context: TurnContext) -> Tuple[int, datetime]:
    """
    Persist a reminder for the current conversation and return (id, due_at_utc).
    """
    due_at = datetime.now(timezone.utc) + timedelta(minutes=int(due_in_minutes))

    # Extract conversation reference using Bot Framework helper
    conv_ref = BfTurnContext.get_conversation_reference(context.activity)
    conv_ref_json = json.dumps(_conv_ref_to_dict(conv_ref))

    with _connect() as con:
        cur = con.execute(
            "INSERT INTO reminders (due_at_utc, text, conversation_ref, sent) VALUES (?, ?, ?, 0)",
            (due_at.isoformat(), text, conv_ref_json),
        )
        con.commit()
        return cur.lastrowid, due_at

async def _send_proactive(adapter: CloudAdapter, conv_ref_dict: Dict[str, Any], message: str):
    async def _callback(tc: TurnContext):
        await tc.send_activity(message)

    ref = _dict_to_conv_ref(conv_ref_dict)
    app_id = APP_ID or ""

    # Try both common continue_conversation signatures, then a claims-based fallback.
    try:
        # Agents SDK variant: (callback, reference, app_id)
        await adapter.continue_conversation(_callback, ref, app_id)
        return
    except Exception:
        pass
    try:
        # BotBuilder-style: (reference, callback, app_id)
        await adapter.continue_conversation(ref, _callback, app_id)
        return
    except Exception:
        pass

    # Fallback: use claims identity API if available
    try:
        claims = getattr(adapter, "create_claims_identity", None)
        cont_with_claims = getattr(adapter, "continue_conversation_with_claims", None)
        if callable(claims) and callable(cont_with_claims):
            identity = claims(app_id)
            # audience can be None in local
            await cont_with_claims(identity, ref, None, _callback)
            return
    except Exception as e:
        # re-raise so the scheduler prints the root cause
        raise e


def setup_scheduler(app: Application, agent_app: AgentApplication) -> None:
    """
    Wire up the AsyncIOScheduler to check for due reminders and send them.
    Called from start_server(..., on_startup=setup_scheduler).
    """
    init_db()

    scheduler = AsyncIOScheduler(
        timezone="UTC",
        job_defaults={"coalesce": True, "max_instances": 1},
    )

    async def tick():
        now = datetime.now(timezone.utc)
        due = fetch_due(now)
        for r in due:
            try:
                await _send_proactive(agent_app.adapter, r.conversation_ref, f"⏰ Reminder: {r.text}")
                mark_sent(r.id)
            except Exception as e:  # pragma: no cover
                # For production code, replace prints with a proper logger.
                print(f"Failed to send reminder {r.id}: {e}")

    # Schedule the coroutine directly (no adapter.loop usage)
    scheduler.add_job(tick, "interval", seconds=10)

    async def _on_startup(_):
        scheduler.start()
        print("Reminder scheduler started")

    async def _on_cleanup(_):
        scheduler.shutdown(wait=False)

    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
