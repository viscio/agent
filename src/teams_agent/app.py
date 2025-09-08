import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

from microsoft_agents.hosting.core import (
    AgentApplication,
    TurnContext,
    TurnState,
    MemoryStorage,
    AgentAuthConfiguration,
    AnonymousTokenProvider,
)
from microsoft_agents.hosting.aiohttp import CloudAdapter

from .start_server import start_server
from .reminders import save_reminder, setup_scheduler
from .llm import ask_with_llm

load_dotenv()

#AUTH_CONFIG = AgentAuthConfiguration(token_provider=AnonymousTokenProvider())
AUTH_CONFIG = AgentAuthConfiguration(
    app_id=os.getenv("MICROSOFT_APP_ID", "local-bot-id"),
    app_password=os.getenv("MICROSOFT_APP_PASSWORD", "local-secret"),
    tenant_id=os.getenv("MICROSOFT_TENANT_ID", ""),
)


ADAPTER = CloudAdapter(auth_configuration=AUTH_CONFIG)
#ADAPTER = CloudAdapter()
#setattr(ADAPTER, "auth_configuration", AUTH_CONFIG)

AGENT_APP = AgentApplication[TurnState](
    storage=MemoryStorage(),
    adapter=ADAPTER,
    #adapter=CloudAdapter(),
)


async def _help(context: TurnContext, _):
    await context.send_activity(
        """
Hi! I'm your Teams agent.

**Commands**
- `echo <text>` — I’ll repeat `<text>`
- `/ask <question>` — (optional) Ask an LLM if OPENAI_API_KEY is set
- `/remind <N> <message>` — I’ll remind you in N minutes

Example: `/remind 5 stand up`
        """
    )


AGENT_APP.conversation_update("membersAdded")(_help)
AGENT_APP.message("/help")(_help)

@AGENT_APP.activity("installationUpdate")
async def _on_installation_update(context: TurnContext, _):
    return

@AGENT_APP.activity("message")
async def on_message(context: TurnContext, _):
    text = (context.activity.text or "").strip()

    # /ask (LLM)
    if text.startswith("/ask"):
        prompt = text[len("/ask"):].strip() or "Say hello"
        try:
            ans = ask_with_llm(prompt)
            if ans is None:
                await context.send_activity(
                    "LLM not configured. Set OPENAI_API_KEY in your .env to enable /ask."
                )
            else:
                await context.send_activity(ans)
        except Exception as e:  # pragma: no cover
            await context.send_activity(f"LLM error: {e}")
        return

    # Remind: /remind <N> <message>
    m = re.match(r"^/remind\s+(\d+)\s+(.+)$", text, re.IGNORECASE)
    if m:
        minutes = int(m.group(1))
        message = m.group(2).strip()
        rid, due_at = save_reminder(minutes, message, context)
        # Show user local time if configured
        tz = os.getenv("LOCAL_TZ", "UTC")
        try:
            local_due = datetime.fromisoformat(due_at.isoformat()).astimezone(ZoneInfo(tz))
            when = local_due.strftime("%H:%M")
            await context.send_activity(f"Got it. I’ll remind you at ~{when} ({tz}). [id={rid}]")
        except Exception:
            await context.send_activity(f"Got it. I’ll remind you in {minutes} minute(s). [id={rid}]")
        return

    # Echo
    if text.lower().startswith("echo "):
        await context.send_activity(text[5:])
        return

    # Default
    await context.send_activity("Try `echo ...`, `/ask ...`, or `/remind N ...` (or `/help`).")


if __name__ == "__main__":
    try:
        start_server(AGENT_APP, None, on_startup=setup_scheduler)
        #start_server(AGENT_APP, AUTH_CONFIG, on_startup=setup_scheduler)
    except Exception as error:
        raise error
