import type {
  ChatMessage,
  Conversation,
  ConversationSearchHit,
  DocSummary,
  FeedbackPayload,
  Health,
  Mode,
  Source,
} from "../types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "";

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const msg = await res.text().catch(() => "");
    throw new Error(msg || `Request failed (${res.status})`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => fetch(`${API_BASE}/api/health`).then((r) => json<Health>(r)),

  chat(message: string, mode: Mode, documents?: string[]) {
    return fetch(`${API_BASE}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, mode, documents: documents ?? [] }),
    }).then((r) => json<ChatMessage>(r));
  },

  conversations: () =>
    fetch(`${API_BASE}/api/conversations`).then((r) => json<Conversation[]>(r)),

  searchConversations: (q: string) =>
    fetch(`${API_BASE}/api/conversations/search?q=${encodeURIComponent(q)}`).then(
      (r) => json<ConversationSearchHit[]>(r),
    ),

  conversationMessages: (id: string) =>
    fetch(`${API_BASE}/api/conversations/${id}/messages`).then((r) =>
      json<ChatMessage[]>(r),
    ),

  deleteConversation: (id: string) =>
    fetch(`${API_BASE}/api/conversations/${id}`, { method: "DELETE" }),

  renameConversation: (id: string, title: string) =>
    fetch(`${API_BASE}/api/conversations/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    }).then((r) => json<Conversation>(r)),

  clearAllConversations: () =>
    fetch(`${API_BASE}/api/conversations`, { method: "DELETE" }),

  feedback: (payload: FeedbackPayload) =>
    fetch(`${API_BASE}/api/feedback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),

  uploadDocument: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return fetch(`${API_BASE}/api/documents/upload`, {
      method: "POST",
      body: form,
    }).then((r) => json<DocSummary>(r));
  },

  documents: () =>
    fetch(`${API_BASE}/api/documents`).then((r) => json<DocSummary[]>(r)),

  deleteDocument: (id: string) =>
    fetch(`${API_BASE}/api/documents/${id}`, { method: "DELETE" }),
};

export interface SSEHandlers {
  onStart?: (start: { conversation_id: string; user_message_id?: string }) => void;
  onStatus?: (status: { status?: string; detail?: string; message?: string }) => void;
  onToolStart?: (tool: { name: string; detail?: string }) => void;
  onToolResult?: (tool: {
    name: string;
    status: string;
    latency_ms?: number;
    error?: string;
  }) => void;
  onSourceFound?: (source: Source) => void;
  onSources?: (sources: Source[]) => void;
  onDelta?: (text: string) => void;
  onSourcesSection?: (text: string) => void;
  onComplete?: (msg: Partial<ChatMessage> & { conversation_id: string }) => void;
  onTitled?: (t: { conversation_id: string; title: string }) => void;
  onError?: (message: string) => void;
  onDone?: () => void;
}

/**
 * POST-based SSE reader (EventSource can't send POST bodies).
 */
export async function streamChat(
  message: string,
  mode: Mode,
  documents: string[],
  handlers: SSEHandlers,
  conversationId?: string | null,
  signal?: AbortSignal,
): Promise<void> {
  const body: Record<string, unknown> = { message, mode, documents };
  if (conversationId) body.conversation_id = conversationId;
  const res = await fetch(`${API_BASE}/api/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) {
    const text = await res.text().catch(() => "");
    handlers.onError?.(text || `Stream failed (${res.status})`);
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const handleEvent = (raw: string) => {
    let event = "message";
    const data: string[] = [];
    for (const line of raw.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).trim());
    }
    if (data.length === 0) return;
    let payload: unknown;
    try {
      payload = JSON.parse(data.join("\n"));
    } catch {
      return; // skip malformed frames instead of crashing the stream
    }
    try {
      const p = payload as Record<string, unknown>;
      switch (event) {
        case "message_start":
          handlers.onStart?.(p as never);
          break;
        case "status":
          handlers.onStatus?.(p as never);
          break;
        case "tool_start":
          handlers.onToolStart?.(p as never);
          break;
        case "tool_result":
          handlers.onToolResult?.(p as never);
          break;
        case "source_found":
          handlers.onSourceFound?.(p as unknown as Source);
          break;
        case "sources":
          handlers.onSources?.((p as unknown as Source[]) ?? []);
          break;
        case "message_delta":
          handlers.onDelta?.(String(p.delta ?? ""));
          break;
        case "sources_section":
          handlers.onSourcesSection?.(String(p.delta ?? ""));
          break;
        case "message_complete":
          handlers.onComplete?.(p as never);
          break;
        case "conversation_titled":
          handlers.onTitled?.({
            conversation_id: String(p.conversation_id ?? ""),
            title: String(p.title ?? ""),
          });
          break;
        case "error":
          handlers.onError?.(String(p.error ?? "Unknown stream error"));
          break;
        case "done":
          handlers.onDone?.();
          break;
      }
    } catch (err) {
      console.error("[SSE] handler error", event, err);
    }
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const chunk = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      if (chunk.startsWith(":")) continue; // SSE comment heartbeat
      handleEvent(chunk.trim());
    }
  }
}