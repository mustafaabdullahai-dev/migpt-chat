import { create } from "zustand";
import type {
  ChatMessage,
  Conversation,
  ConversationSearchHit,
  DocSummary,
  Mode,
  ResearchPhase,
  Source,
  ToolRun,
} from "../types";
import { api, streamChat } from "../services/api";

interface ChatState {
  conversations: Conversation[];
  searchResults: ConversationSearchHit[] | null;
  currentId: string | null;
  messages: Record<string, ChatMessage[]>;
  documents: DocSummary[];
  theme: "dark" | "light";
  mode: Mode;
  sending: boolean;
  streamingText: string;
  streamingStatus: string;
  streamingSources: Source[];
  streamingTools: ToolRun[];
  healthError: string | null;
  sidebarOpen: boolean;
  activity: ResearchPhase[];

  setTheme: (t: "dark" | "light") => void;
  setMode: (m: Mode) => void;
  toggleSidebar: () => void;
  openSidebar: () => void;
  closeSidebar: () => void;
  loadConversations: () => Promise<void>;
  searchChats: (q: string) => Promise<void>;
  selectConversation: (id: string) => Promise<void>;
  newConversation: () => void;
  renameConversation: (id: string, title: string) => Promise<void>;
  deleteConversation: (id: string) => Promise<void>;
  clearAllConversations: () => Promise<void>;
  loadDocuments: () => Promise<void>;
  uploadDocument: (f: File) => Promise<void>;
  deleteDocument: (id: string) => Promise<void>;
  send: (text: string, docs?: string[]) => Promise<void>;
  stop: () => void;
  rate: (convId: string, msgId: string, rating: "up" | "down") => Promise<void>;
}

export const useChat = create<ChatState>((set, get) => {
  let controller: AbortController | null = null;

  const freshMessage = (role: "user" | "assistant", convId: string): ChatMessage => ({
    message_id: `local-${crypto.randomUUID()}`,
    conversation_id: convId,
    role,
    content: "",
    sources: [],
    tool_runs: [],
    created_at: new Date().toISOString(),
    is_streaming: role === "assistant",
  });

  const upsertLocal = (convId: string, msg: ChatMessage) => {
    const current = get().messages[convId] ?? [];
    const idx = current.findIndex((m) => m.message_id === msg.message_id);
    const next =
      idx === -1
        ? [...current, msg]
        : current.map((m) => (m.message_id === msg.message_id ? msg : m));
    set({ messages: { ...get().messages, [convId]: next } });
  };

// ── High-level research phases ────────────────────────────────────────────
  // Only the LAST phase pulses; earlier ones flatten to a checkmark + count.
  // Individual tool calls are folded into these phases and never shown raw.
  const PHASES: Record<ResearchPhase["id"], ResearchPhase["label"]> = {
    searching: "Searching the web",
    reading: "Reading sources",
    synthesizing: "Synthesizing findings",
  };

  // Make `id` the active (pulsing) phase; all others become done.
  const phaseTransition = (
    prev: ResearchPhase[],
    id: ResearchPhase["id"],
  ): ResearchPhase[] => {
    const order: ResearchPhase["id"][] = ["searching", "reading", "synthesizing"];
    const out: ResearchPhase[] = [];
    for (const pid of order) {
      const existing = prev.find((p) => p.id === pid);
      if (pid === id) {
        out.push({
          id: pid,
          label: PHASES[pid],
          status: "active",
          count: existing?.count,
        });
      } else if (existing) {
        out.push({ ...existing, status: "done" });
      }
    }
    return out;
  };

  const statusToPhase = (s: string): ResearchPhase["id"] | null => {
    if (s.includes("document")) return null; // doc flows keep their plain status line
    if (["synthesiz", "generating your answer", "writing", "structuring mirror"].some((w) => s.includes(w)))
      return "synthesizing";
    if (["reading", "analyzing", "gathering", "fetching sources"].some((w) => s.includes(w)))
      return "reading";
    if (["search", "looking up", "brows", "background references"].some((w) => s.includes(w)))
      return "searching";
    return null;
  };

  const applyStatus = (raw: string) => {
    const id = statusToPhase(String(raw ?? "").toLowerCase());
    if (!id) return;
    set((prev) => ({ activity: phaseTransition(prev.activity, id) }));
  };

  const onActivityToolStart = (name: string) => {
    const id =
      name === "fetch_webpage"
        ? "reading"
        : ["web_search", "wikipedia", "arxiv", "deep_research"].includes(name)
          ? "searching"
          : null;
    if (!id) return;
    set((prev) => ({ activity: phaseTransition(prev.activity, id) }));
  };

  const onActivityToolResult = (name: string, status: string) => {
    if (name === "fetch_webpage" && status === "success") {
      set((prev) => ({
        activity: prev.activity.map((p) =>
          p.id === "reading" ? { ...p, count: (p.count ?? 0) + 1 } : p,
        ),
      }));
    }
  };

  return {
    conversations: [],
    searchResults: null,
    currentId: null,
    messages: {},
    documents: [],
    theme: "dark",
    mode: "auto",
    sending: false,
    streamingText: "",
    streamingStatus: "",
    streamingSources: [],
    streamingTools: [],
    healthError: null,
    sidebarOpen: false,
    activity: [],

    setTheme: (t) => {
      set({ theme: t });
      document.documentElement.classList.toggle("dark", t === "dark");
      localStorage.setItem("theme", t);
    },

    setMode: (m) => set({ mode: m }),

    toggleSidebar: () => set((prev) => ({ sidebarOpen: !prev.sidebarOpen })),
    openSidebar: () => set({ sidebarOpen: true }),
    closeSidebar: () => set({ sidebarOpen: false }),

    loadConversations: async () => {
      try {
        const convs = await api.conversations();
        set({ conversations: convs, healthError: null });
      } catch {
        set({ healthError: "Backend unreachable — start the FastAPI server (port 8000)." });
      }
    },

    searchChats: async (q) => {
      const trim = q.trim();
      if (!trim) {
        set({ searchResults: null });
        return;
      }
      try {
        set({ searchResults: await api.searchConversations(trim) });
      } catch {
        set({ searchResults: [] });
      }
    },

    selectConversation: async (id) => {
      set({ currentId: id, sidebarOpen: false });
      if (!get().messages[id]) {
        try {
          const msgs = await api.conversationMessages(id);
          set({ messages: { ...get().messages, [id]: msgs } });
        } catch {
          /* ignore */
        }
      }
    },

    newConversation: () => set({ currentId: null, sidebarOpen: false }),

    renameConversation: async (id, title) => {
      const conv = await api.renameConversation(id, title);
      set({
        conversations: get().conversations.map((c) =>
          c.conversation_id === id ? conv : c,
        ),
      });
    },

    deleteConversation: async (id) => {
      await api.deleteConversation(id);
      const msgs = { ...get().messages };
      delete msgs[id];
      set({
        conversations: get().conversations.filter((c) => c.conversation_id !== id),
        messages: msgs,
        currentId: get().currentId === id ? null : get().currentId,
      });
    },

    clearAllConversations: async () => {
      await api.clearAllConversations();
      set({ conversations: [], messages: {}, currentId: null, sidebarOpen: false });
    },

    loadDocuments: async () => {
      try {
        set({ documents: await api.documents() });
      } catch {
        /* ignore */
      }
    },

    uploadDocument: async (file) => {
      const doc = await api.uploadDocument(file);
      set({ documents: [...get().documents, doc] });
    },

    deleteDocument: async (id) => {
      await api.deleteDocument(id);
      set({ documents: get().documents.filter((d) => d.document_id !== id) });
    },

    send: async (text, docs = []) => {
      if (get().sending || !text.trim()) return;
      const attachSnapshot = [...get().documents];
      // Every chat gets a unique client UUID up front (even a brand-new one) so
      // messages stream under an id owned by this chat from the first token —
      // chats never share a placeholder key.
      const isNew = get().currentId === null;
      const convId = get().currentId ?? crypto.randomUUID();

      set({ sending: true, streamingText: "", streamingStatus: "Thinking…", streamingSources: [], streamingTools: [], activity: [] });
      controller = new AbortController();

      // Optimistically add the user message; the assistant reply streams live below
      const userMsg = freshMessage("user", convId);
      userMsg.content = text;
      userMsg.attachments = attachSnapshot.length > 0 ? attachSnapshot : undefined;
      const assistMsg = freshMessage("assistant", convId);
      upsertLocal(convId, userMsg);
      if (isNew) set({ currentId: convId });

      // The uploaded docs are now bound to this message (and shown on its bubble);
      // clear the composer's attachment chips so they don't leak into other chats.
      if (attachSnapshot.length > 0) set({ documents: [] });

      try {
        await streamChat(
          text,
          get().mode,
          docs,
          {
            onStart: () =>
              set({
                streamingText: "",
                streamingStatus: "Preparing…",
                streamingTools: [],
                streamingSources: [],
              }),
            onStatus: (s) => {
              const label = String(s.message ?? s.status ?? "Working").trim();
              if (!label) return;
              applyStatus(label);
              set({ streamingStatus: label });
            },
            onToolStart: (t) => {
              onActivityToolStart(t.name);
              set({
                streamingTools: [
                  ...get().streamingTools,
                  {
                    tool: t.name,
                    arguments: String(t.detail ?? ""),
                    started_at: new Date().toISOString(),
                    status: "running",
                  },
                ],
              });
            },
            onToolResult: (t) => {
              onActivityToolResult(t.name, t.status);
              const tools = [...get().streamingTools].reverse();
              const idx = tools.findIndex((r) => r.tool === t.name && r.status === "running");
              const flatIdx = idx === -1 ? -1 : get().streamingTools.length - 1 - idx;
              if (flatIdx === -1) return;
              const next = [...get().streamingTools];
              next[flatIdx] = {
                ...next[flatIdx],
                status: t.status === "error" ? "error" : "success",
                duration_ms: t.latency_ms,
                error: t.error,
              };
              set({ streamingTools: next });
            },
            onSourceFound: (s) => {
              const srcs = [...get().streamingSources];
              const seen = new Set(srcs.map((x) => x.source_id));
              if (!seen.has(s.source_id)) {
                srcs.push(s);
                set({
                  streamingSources: srcs,
                  activity: get().activity.map((p) =>
                    p.id === "searching" ? { ...p, count: srcs.length } : p,
                  ),
                });
              }
            },
            onSources: (srcs) => set({ streamingSources: Array.isArray(srcs) ? srcs : [] }),
            onDelta: (d) => set({ streamingText: get().streamingText + d }),
            onSourcesSection: (d) => set({ streamingText: get().streamingText + d }),
            onComplete: (msg) => {
              const completed: ChatMessage = {
                message_id: msg.message_id ?? `msg-${crypto.randomUUID()}`,
                conversation_id: msg.conversation_id ?? convId,
                role: "assistant",
                content: String(msg.content ?? get().streamingText ?? ""),
                sources: Array.isArray(msg.sources) ? msg.sources : [],
                tool_runs: Array.isArray(msg.tool_runs) ? msg.tool_runs : get().streamingTools,
                followups: Array.isArray(msg.followups) ? msg.followups : [],
                intent: msg.intent,
                created_at: msg.created_at ?? new Date().toISOString(),
                is_streaming: false,
              };
              upsertLocal(completed.conversation_id, completed);
              // Rescue path: if the backend had to fall back to a different id
              // (e.g. an extremely unlikely UUID collision), migrate the optimistic
              // user message so it isn't orphaned under the local key.
              if (convId !== completed.conversation_id) {
                const draft = { ...get().messages };
                const migrated = draft[convId] ?? [];
                delete draft[convId];
                draft[completed.conversation_id] = [
                  ...migrated.filter((m) => m.message_id !== completed.message_id),
                  ...(draft[completed.conversation_id] ?? []),
                ];
                set({ messages: draft });
              }
              set({
                streamingText: "",
                streamingStatus: "",
                streamingSources: [],
                streamingTools: [],
                currentId: completed.conversation_id,
              });
            },
            onError: (err) => {
              upsertLocal(convId, {
                ...assistMsg,
                content: String(err),
                tool_runs: get().streamingTools,
                is_streaming: false,
              });
              set({ streamingStatus: "" });
            },
            onTitled: (t) => {
              const now = new Date().toISOString();
              const exists = get().conversations.some((c) => c.conversation_id === t.conversation_id);
              if (exists) {
                set({
                  conversations: get().conversations.map((c) =>
                    c.conversation_id === t.conversation_id ? { ...c, title: t.title } : c,
                  ),
                });
              } else {
                set({
                  conversations: [
                    {
                      conversation_id: t.conversation_id,
                      title: t.title,
                      created_at: now,
                      updated_at: now,
                    },
                    ...get().conversations,
                  ],
                });
              }
            },
            onDone: async () => {
              set({
                sending: false,
                streamingText: "",
                streamingStatus: "",
                streamingSources: [],
                streamingTools: [],
                activity: [],
              });
              await get().loadConversations();
            },
          },
          convId, // always a unique UUID (client-generated for new chats)
          controller.signal,
        );
      } catch {
        // aborted or network failure
      } finally {
        if (controller?.signal.aborted) {
          set({ sending: false, streamingText: "", streamingStatus: "", streamingTools: [], activity: [] });
        }
      }
    },

    stop: () => {
      controller?.abort();
      set({ sending: false, streamingText: "", streamingStatus: "", streamingTools: [], activity: [] });
    },

    rate: async (convId, msgId, rating) => {
      try {
        await api.feedback({ conversation_id: convId, message_id: msgId, rating });
      } catch {
        /* ignore */
      }
    },
  };
});