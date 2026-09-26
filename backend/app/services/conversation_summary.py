"""Rolling per-conversation summaries for long-chat context management.

A conversation that grows long must never be sent whole to the model. This
service maintains a compact running summary per conversation, refreshed in the
background every few turns, which the synthesizer injects ahead of the recent
message window.
"""

from __future__ import annotations

import asyncio

from app.core.logging import get_logger
from app.services.llm import get_fast_llm
from app.services.summarizer import format_conversation

logger = get_logger("conversation_summary")

RECENT_FOR_ROLL = 8  # last turns folded into the running summary each refresh
TARGET_WORDS = 120
MAX_SUMMARY_CHARS = 8000

_SUMMARY_PROMPT = (
    "You maintain a running summary of a user's chat for mid-conversation context.\n\n"
    "PREVIOUS SUMMARY (may be empty if this is the first):\n{prev}\n\n"
    "NEW CHAT TURNS SINCE THEN (most recent included; ignore trivial greetings):\n{turns}\n\n"
    "Write the updated summary: one lead sentence about the overall topic, then short bullets "
    "of the subjects discussed, the user's stated preferences or personal details, and any "
    "conclusions reached. Keep the user's facts verbatim when short. Stay within {words} words. "
    "Do not invent anything. Output only the summary."
)


def summary_due(message_count: int, every: int = 5, minimum: int = 6) -> bool:
    """Summarize at the first milestone, then every `every` messages after it."""
    return message_count == minimum or (
        message_count >= minimum and message_count % every == 0
    )


async def revise_summary(prev: str, history: list[dict]) -> str:
    """Fold the latest turns into the running summary via the LLM."""
    turns = format_conversation(history[-RECENT_FOR_ROLL:])
    prev_text = (prev or "").strip() or "(none yet)"
    prompt = _SUMMARY_PROMPT.format(
        prev=prev_text, turns=turns or "(no messages)", words=TARGET_WORDS
    )
    try:
        llm = get_fast_llm(temperature=0.2, max_tokens=400)
        resp = await llm.ainvoke([("human", prompt)])
        summary = str(getattr(resp, "content", "") or "").strip()
    except Exception as exc:
        logger.warning(
            "conversation summary refresh failed; keeping previous",
            extra={"event": "conversation_summary.fail", "error": str(exc)},
        )
        return (prev or "").strip()
    return summary[:MAX_SUMMARY_CHARS]


def schedule_refresh(db, conversation_id: str) -> None:
    """Fire-and-forget a summary refresh when a conversation becomes large.

    Runs in its own session/task so it never delays the streamed reply. Inert
    when summarization is disabled or no event loop is running (sync context).
    """
    from app.core.config import settings
    from app.db.crud import count_messages

    if not settings.enable_conversation_summaries:
        return
    if not summary_due(
        count_messages(db, conversation_id),
        every=settings.conversation_summary_every,
        minimum=settings.conversation_summary_min,
    ):
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(_background_refresh(conversation_id))


async def _background_refresh(conversation_id: str) -> None:
    from app.db.base import session_scope
    from app.db.crud import list_messages, update_conversation_summary
    from app.db.models import Conversation

    try:
        with session_scope() as db:
            msgs = list_messages(db, conversation_id)
            conv = db.get(Conversation, conversation_id)
            prev = conv.summary if conv else ""
            turns = [{"role": m.role, "content": m.content} for m in msgs]
            summary = await revise_summary(prev, turns)
            update_conversation_summary(db, conversation_id, summary)
            logger.info(
                "conversation summary refreshed",
                extra={"event": "conversation_summary.updated", "conversation_id": conversation_id},
            )
    except Exception as exc:
        logger.warning(
            "conversation summary background refresh failed",
            extra={"event": "conversation_summary.background_fail", "error": str(exc)},
        )