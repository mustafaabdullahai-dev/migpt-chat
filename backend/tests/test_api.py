"""API integration tests (demo/mock mode — fully offline)."""

from __future__ import annotations

import uuid


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["llm"] == "mock"
    assert body["calculator"] is True


def test_chat_basic_flow(client):
    resp = client.post("/api/chat", json={"message": "What is LangGraph?"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["conversation_id"]
    assert body["answer"]
    assert body["intent"] in ("GENERAL_CHAT", "CODE", "BACKGROUND_KNOWLEDGE")


def test_chat_calculation(client):
    resp = client.post("/api/chat", json={"message": "Calculate 2**10"})
    assert resp.status_code == 200
    assert "1024" in resp.json()["answer"]


def test_conversation_lifecycle(client):
    chat = client.post("/api/chat", json={"message": "Hello"}).json()
    conv_id = chat["conversation_id"]

    convs = client.get("/api/conversations")
    assert convs.status_code == 200
    assert any(c["conversation_id"] == conv_id for c in convs.json())

    msgs = client.get(f"/api/conversations/{conv_id}/messages")
    assert msgs.status_code == 200
    assert len(msgs.json()) == 2  # user + assistant

    ren = client.patch(f"/api/conversations/{conv_id}", json={"title": "My title"})
    assert ren.status_code == 200
    assert ren.json()["title"] == "My title"

    dele = client.delete(f"/api/conversations/{conv_id}")
    assert dele.status_code == 204


def test_chat_stream_sse(client):
    with client.stream("POST", "/api/chat/stream", json={"message": "Count to 3"}) as resp:
        assert resp.status_code == 200
        body = resp.read().decode()
        assert "event: status" in body
        assert "message_delta" in body
        assert "done" in body


def test_feedback(client):
    chat = client.post("/api/chat", json={"message": "Hi"}).json()
    conv_id = chat["conversation_id"]
    assistant = client.get(f"/api/conversations/{conv_id}/messages").json()[1]
    fb = client.post(
        "/api/feedback",
        json={"conversation_id": conv_id, "message_id": assistant["message_id"], "rating": "up"},
    )
    assert fb.status_code == 201


def test_document_upload_and_rag(client):
    content = b"RAG is a technique to retrieve relevant chunks and feed them to an LLM. It reduces hallucination."
    resp = client.post(
        "/api/documents/upload",
        files={"file": ("notes.txt", content, "text/plain")},
    )
    assert resp.status_code == 200, resp.text
    doc = resp.json()
    assert doc["chunks"] >= 1

    docs = client.get("/api/documents")
    assert any(d["document_id"] == doc["document_id"] for d in docs.json())

    dele = client.delete(f"/api/documents/{doc['document_id']}")
    assert dele.status_code == 204


def test_bad_upload_rejected(client):
    resp = client.post(
        "/api/documents/upload",
        files={"file": ("evil.exe", b"MZ...", "application/octet-stream")},
    )
    assert resp.status_code == 400


def test_client_supplied_uuid_creates_its_own_conversation(client):
    conv_id = str(uuid.uuid4())
    resp = client.post("/api/chat", json={"message": "Say hi", "conversation_id": conv_id})
    assert resp.status_code == 200, resp.text
    assert resp.json()["conversation_id"] == conv_id
    convs = client.get("/api/conversations").json()
    assert any(c["conversation_id"] == conv_id for c in convs)


def test_followup_reuses_same_conversation(client):
    conv_id = str(uuid.uuid4())
    first = client.post("/api/chat", json={"message": "Hello", "conversation_id": conv_id}).json()
    second = client.post(
        "/api/chat", json={"message": "And again", "conversation_id": conv_id}
    ).json()
    assert first["conversation_id"] == second["conversation_id"] == conv_id
    msgs = client.get(f"/api/conversations/{conv_id}/messages").json()
    assert len(msgs) == 4  # hello (user+assistant) then follow-up (user+assistant)


def test_invalid_conversation_id_rejected(client):
    resp = client.post("/api/chat", json={"message": "Hi", "conversation_id": "not-a-uuid"})
    assert resp.status_code == 400