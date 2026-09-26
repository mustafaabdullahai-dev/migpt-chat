"""Conversation memory: metadata, summaries, search + context management (offline)."""

from __future__ import annotations

from sqlalchemy import Column, String, Text, create_engine

from app.db.base import SessionLocal, _auto_migrate, init_db
from app.db.crud import (
    add_message,
    conv_metadata,
    conv_stats,
    create_conversation,
    patch_conversation_metadata,
    search_conversations,
    update_conversation_summary,
)
from app.services.conversation_summary import summary_due

import pytest

pytestmark = pytest.mark.usefixtures("_db_tables")


@pytest.fixture(scope="module", autouse=True)
def _db_tables():
    init_db()


def _fresh_user() -> str:
    import uuid

    return f"convmem-{uuid.uuid4().hex[:8]}"


def test_conv_metadata_roundtrip_and_merge():
    with SessionLocal() as db:
        conv = create_conversation(db, _fresh_user())
        patch_conversation_metadata(db, conv.conversation_id, {"pinned": True, "origin": "sidebar"})
        patch_conversation_metadata(db, conv.conversation_id, {"pinned": False, "extra": 3})
        meta = conv_metadata(db.get(type(conv), conv.conversation_id))
        assert meta == {"pinned": False, "origin": "sidebar", "extra": 3}
        assert conv_metadata(db.get(type(conv), conv.conversation_id))["extra"] == 3


def test_conv_metadata_tolerates_corrupt_json():
    with SessionLocal() as db:
        conv = create_conversation(db, _fresh_user())
        conv.metadata_json = "not:json{{"
        db.commit()
        assert conv_metadata(db.get(type(conv), conv.conversation_id)) == {}


def test_conv_stats_counts_and_last_message():
    with SessionLocal() as db:
        conv = create_conversation(db, _fresh_user())
        add_message(db, conv.conversation_id, "user", "first question")
        add_message(db, conv.conversation_id, "assistant", "first answer")
        add_message(db, conv.conversation_id, "user", "second question")
        stats = conv_stats(db, [conv.conversation_id])
        assert stats[conv.conversation_id]["count"] == 3
        assert stats[conv.conversation_id]["last"] == "second question"


def test_search_finds_by_title_and_by_message_content():
    with SessionLocal() as db:
        uid = _fresh_user()
        by_title = create_conversation(db, uid, title="LangGraph orchestration")
        by_content = create_conversation(db, uid, title="Misc")
        add_message(db, by_content.conversation_id, "user", "Please compare FastAPI with LangChain")
        add_message(db, by_content.conversation_id, "assistant", "Here is a comparison.")

        by_title_hits = search_conversations(db, uid, "LangGraph")
        assert [h["conv"].conversation_id for h in by_title_hits] == [by_title.conversation_id]

        by_content_hits = search_conversations(db, uid, "FastAPI")
        assert [h["conv"].conversation_id for h in by_content_hits] == [by_content.conversation_id]
        assert "FastAPI" in by_content_hits[0]["snippet"]
        assert by_content_hits[0]["snippet_role"] == "user"

        no_hits = search_conversations(db, uid, "zebra-unicorn-xyz")
        assert no_hits == []

        other_user = search_conversations(db, _fresh_user(), "LangGraph")
        assert other_user == []


def test_summary_due_gating():
    assert not summary_due(5)
    assert summary_due(6) and summary_due(10) and summary_due(15)
    assert not summary_due(7)
    assert not summary_due(4)
    assert summary_due(20, every=10, minimum=6)
    assert summary_due(20, every=5, minimum=6)
    assert not summary_due(5, every=5, minimum=6)


def test_update_conversation_summary_roundtrip():
    with SessionLocal() as db:
        conv = create_conversation(db, _fresh_user())
        update_conversation_summary(db, conv.conversation_id, "User discussed LangGraph and planning.")
        refreshed = db.get(type(conv), conv.conversation_id)
        assert refreshed.summary == "User discussed LangGraph and planning."
        update_conversation_summary(db, conv.conversation_id, None)
        assert db.get(type(conv), conv.conversation_id).summary == ""


def test_auto_migrate_adds_missing_columns():
    from sqlalchemy import MetaData, Table, inspect

    md = MetaData()
    Table(
        "conversations",
        md,
        Column("conversation_id", String(36), primary_key=True),
        Column("user_id", String(64)),
        Column("title", String(200)),
        Column("summary", Text),
        Column("metadata_json", Text),
    )

    tmp = create_engine("sqlite:////tmp/chatgpt-migrate-test.db")
    md.drop_all(tmp)
    md.create_all(tmp)

    # simulate a pre-existing DB that lacks the two columns
    with tmp.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE conversations DROP COLUMN summary")
        conn.exec_driver_sql("ALTER TABLE conversations DROP COLUMN metadata_json")

    before = {c["name"] for c in inspect(tmp).get_columns("conversations")}
    assert "summary" not in before and "metadata_json" not in before

    _auto_migrate(bind=tmp)
    after = {c["name"] for c in inspect(tmp).get_columns("conversations")}
    assert "summary" in after and "metadata_json" in after

    # idempotent: running again is a no-op
    _auto_migrate(bind=tmp)
    md.drop_all(tmp)


def test_conversation_out_exposes_memory_fields(client):
    import uuid

    uid = f"api-{uuid.uuid4().hex[:8]}"
    headers = {"X-User-Id": uid}
    chat = client.post("/api/chat", json={"message": "Hello from memory test"}, headers=headers).json()
    conv_id = chat["conversation_id"]
    with SessionLocal() as db:
        patch_conversation_metadata(db, conv_id, {"favorite": "coffee"})
        update_conversation_summary(db, conv_id, "Summary: greeted the assistant.")

    body = client.get(f"/api/conversations/{conv_id}", headers=headers).json()
    assert body["metadata"] == {"favorite": "coffee"}
    assert body["summary"] == "Summary: greeted the assistant."
    assert body["message_count"] == 2
    assert body["last_message"]


def test_conversation_patch_updates_metadata_and_summary(client):
    import uuid

    uid = f"api-{uuid.uuid4().hex[:8]}"
    headers = {"X-User-Id": uid}
    conv_id = client.post("/api/conversations", headers=headers).json()["conversation_id"]

    resp = client.patch(
        f"/api/conversations/{conv_id}",
        json={"title": "Renamed", "metadata": {"pinned": True}, "summary": "Short summary."},
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "Renamed"
    assert body["metadata"] == {"pinned": True}
    assert body["summary"] == "Short summary."

    # metadata patch merges rather than replaces
    resp2 = client.patch(
        f"/api/conversations/{conv_id}",
        json={"metadata": {"starred": 1}},
        headers=headers,
    )
    assert resp2.json()["metadata"] == {"pinned": True, "starred": 1}
    assert resp2.json()["title"] == "Renamed"  # untouched


def test_search_api_endpoint(client):
    import uuid

    uid = f"api-{uuid.uuid4().hex[:8]}"
    headers = {"X-User-Id": uid}
    client.post("/api/conversations", headers=headers)
    chat = client.post(
        "/api/chat", json={"message": "Explain quantum entanglement and teleportation"}, headers=headers
    ).json()
    conv_id = chat["conversation_id"]

    hits = client.get(f"/api/conversations/search?q={conv_id and 'quantum'}", headers=headers)
    assert hits.status_code == 200
    body = [h for h in hits.json() if h["conversation"]["conversation_id"] == conv_id]
    assert body and "quantum" in body[0]["snippet"].lower() and "teleportation" in body[0]["snippet"]

    none = client.get("/api/conversations/search?q=no-such-term-xyz", headers=headers)
    assert none.json() == []


def test_synthesize_injects_conversation_summary_and_trims_window():
    from app.agents.nodes.synthesize import _build_messages
    from langchain_core.messages import HumanMessage, SystemMessage

    history = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i} " + "x" * 300}
        for i in range(40)
    ]
    with_summary = _build_messages(
        {
            "query": "What next?",
            "history": history,
            "conversation_summary": "User explored LangGraph state machines and streaming.",
        }
    )
    system = with_summary[0]
    assert isinstance(system, SystemMessage)
    text = str(system.content)
    assert "CONVERSATION SO FAR" in text
    assert "LangGraph state machines" in text

    human_msgs = [m for m in with_summary if isinstance(m, HumanMessage) and m.content != "What next?"]
    without_summary = _build_messages({"query": "What next?", "history": history})
    human_msgs_no = [
        m for m in without_summary if isinstance(m, HumanMessage) and m.content != "What next?"
    ]

    # summary lets the verbatim window shrink (fewer old turns, less total context)
    assert 0 < len(human_msgs) < len(human_msgs_no)
    assert sum(len(str(m.content)) for m in human_msgs) < sum(len(str(m.content)) for m in human_msgs_no)