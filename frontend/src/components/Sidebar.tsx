import { useEffect, useMemo, useRef, useState } from "react";
import {
  Search,
  Plus,
  MessageSquare,
  Folder,
  Settings,
  HelpCircle,
  MoreHorizontal,
  PanelLeftClose,
  User,
  LogOut,
  Trash2,
  Pencil,
  Moon,
  Sun,
  Calculator,
  Globe,
  ArrowRightLeft,
  FileText,
} from "lucide-react";
import { useChat } from "../store/useChat";
import type { Conversation } from "../types";
import migptLogo from "../assets/migpt.png";

function groupLabel(ts?: string): string {
  if (!ts) return "Other";
  const days = (Date.now() - new Date(ts).getTime()) / 86400000;
  if (days <= 1) return "Today";
  if (days <= 7) return "Previous 7 Days";
  if (days <= 30) return "Previous 30 Days";
  return "Older";
}

const FEATURES = [
  {
    icon: Calculator,
    label: "Calculate",
    prompt: "Calculate 17.5% of 85,000 and show the steps",
  },
  {
    icon: Globe,
    label: "Research",
    prompt: "Research the latest developments in agentic AI",
  },
  {
    icon: ArrowRightLeft,
    label: "Compare",
    prompt: "Compare FastAPI vs Flask for building production APIs",
  },
  {
    icon: FileText,
    label: "Summarize",
    prompt: "Summarize the key points of LangGraph",
  },
];

function ChatRow({
  conv,
  active,
  menuOpen,
  onSelect,
  onMenu,
  onRename,
  onDelete,
}: {
  conv: Conversation;
  active: boolean;
  menuOpen: boolean;
  onSelect: () => void;
  onMenu: () => void;
  onRename: () => void;
  onDelete: () => void;
}) {
  return (
    <div className="group relative">
      <button
        onClick={onSelect}
        className={`flex w-full items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-[13px] transition ${
          active ? "bg-white/15 text-white" : "text-neutral-300 hover:bg-white/10 hover:text-white"
        }`}
      >
        <MessageSquare size={13} className="shrink-0 text-neutral-500" />
        <span className="min-w-0 flex-1 truncate">{conv.title}</span>
        <MoreHorizontal
          size={15}
          onClick={(e) => {
            e.stopPropagation();
            onMenu();
          }}
          className={`shrink-0 ${menuOpen ? "block text-white" : "hidden group-hover:block text-neutral-500"}`}
        />
      </button>

      {menuOpen && (
        <div className="absolute right-2 top-8 z-50 w-36 rounded-lg border border-white/10 bg-[#1c1c1c] p-1 shadow-lg">
          <button
            onClick={onRename}
            className="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-xs text-neutral-200 hover:bg-white/10"
          >
            <Pencil size={13} />
            Rename
          </button>
          <button
            onClick={onDelete}
            className="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-xs text-red-400 hover:bg-red-500/10"
          >
            <Trash2 size={13} />
            Delete
          </button>
        </div>
      )}
    </div>
  );
}

function SidebarContent({ onClose }: { onClose?: () => void }) {
  const {
    conversations,
    searchResults,
    currentId,
    loadConversations,
    searchChats,
    selectConversation,
    newConversation,
    renameConversation,
    deleteConversation,
    clearAllConversations,
    theme,
    setTheme,
  } = useChat();
  const [query, setQuery] = useState("");
  const [chatMenu, setChatMenu] = useState<string | null>(null);
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    loadConversations();
  }, [loadConversations]);

  useEffect(() => {
    if (searchTimer.current) clearTimeout(searchTimer.current);
    searchTimer.current = setTimeout(() => {
      searchChats(query);
    }, 250);
    return () => {
      if (searchTimer.current) clearTimeout(searchTimer.current);
    };
  }, [query, searchChats]);

  const searching = query.trim().length > 0;

  const groups = useMemo(() => {
    if (searching) return [];
    const order = ["Today", "Previous 7 Days", "Previous 30 Days", "Other"];
    const map = new Map<string, Conversation[]>();
    for (const c of conversations) {
      const g = groupLabel(c.updated_at ?? c.created_at);
      if (!map.has(g)) map.set(g, []);
      map.get(g)!.push(c);
    }
    return order.map((g) => ({ label: g, items: map.get(g) ?? [] })).filter((g) => g.items.length > 0);
  }, [conversations, searching]);

  const newChat = () => {
    newConversation();
    onClose?.();
  };

  const select = (id: string) => {
    selectConversation(id);
    setChatMenu(null);
    onClose?.();
  };

  const runFeature = (prompt: string) => {
    useChat.getState().send(prompt);
    setQuery("");
    onClose?.();
  };

  return (
    <div className="flex h-full w-full flex-col text-white">
      {/* Sidebar Top */}
      <div className="flex h-12 items-center justify-between px-3">
        <div className="flex items-center gap-2">
          <img src={migptLogo} alt="MiGPT" className="h-6 w-6 rounded-md object-cover" />
          <span className="flex items-center gap-1 text-[14px] font-semibold tracking-tight">
            MiGPT
          </span>
        </div>
        <div className="flex items-center gap-0.5">
          <button
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            className="flex h-7 w-7 items-center justify-center rounded-md text-neutral-400 hover:bg-white/10"
            title={theme === "dark" ? "Switch to light" : "Switch to dark"}
          >
            {theme === "dark" ? <Sun size={15} /> : <Moon size={15} />}
          </button>
          <button
            onClick={onClose}
            className="flex h-7 w-7 items-center justify-center rounded-md text-neutral-400 hover:bg-white/10"
            title="Close sidebar"
          >
            <PanelLeftClose size={16} />
          </button>
        </div>
      </div>

      {/* New Chat */}
      <div className="px-2">
        <button
          onClick={newChat}
          className="flex w-full items-center gap-2.5 rounded-lg border border-white/10 px-3 py-2 text-[13px] font-medium text-white transition hover:bg-white/10"
        >
          <Plus size={15} />
          <span>New chat</span>
        </button>
      </div>

      {/* Features (Calculate / Research / Compare / Summarize) */}
      <div className="mt-2.5 px-2">
        <div className="grid grid-cols-2 gap-1.5">
          {FEATURES.map((f) => (
            <button
              key={f.label}
              onClick={() => runFeature(f.prompt)}
              className="flex items-center gap-1.5 rounded-lg bg-white/5 px-2.5 py-1.5 text-left text-xs text-neutral-300 transition hover:bg-white/15 hover:text-white"
              title={f.prompt}
            >
              <f.icon size={13} className="shrink-0 text-neutral-400" />
              <span className="truncate">{f.label}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Search */}
      <div className="px-2 pt-2">
        <div className="flex w-full items-center gap-2 rounded-lg bg-white/10 px-2.5 py-1.5">
          <Search size={14} className="shrink-0 text-neutral-400" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search chats"
            className="min-w-0 flex-1 bg-transparent text-[13px] text-white outline-none placeholder:text-neutral-500"
          />
          <span className="ml-auto text-[10px] text-neutral-500">⌘K</span>
        </div>
      </div>

      {/* Projects */}
      <div className="pt-1.5">
        <button className="flex w-full items-center gap-2.5 rounded-lg px-3 py-1.5 text-[13px] text-neutral-300 hover:bg-white/10">
          <Folder size={14} />
          <span>Projects</span>
        </button>
      </div>

      {/* Chat History */}
      <div className="mt-2 flex-1 overflow-y-auto px-1.5">
        {!searching && conversations.length === 0 && (
          <p className="px-3 py-6 text-center text-xs text-neutral-500">No chats yet</p>
        )}
        {searching && searchResults !== null && searchResults.length === 0 && (
          <p className="px-3 py-6 text-center text-xs text-neutral-500">No matches found</p>
        )}

        {groups.map((g) => (
          <div key={g.label}>
            <div className="px-2.5 pb-1 pt-1.5 text-[11px] font-medium text-neutral-500">{g.label}</div>
            <div className="space-y-0.5">
              {g.items.map((c) => (
                <ChatRow
                  key={c.conversation_id}
                  conv={c}
                  active={c.conversation_id === currentId}
                  menuOpen={chatMenu === c.conversation_id}
                  onSelect={() => select(c.conversation_id)}
                  onMenu={() =>
                    setChatMenu(chatMenu === c.conversation_id ? null : c.conversation_id)
                  }
                  onRename={() => {
                    setChatMenu(null);
                    const t = window.prompt("Rename chat", c.title);
                    if (t && t.trim() && t !== c.title) renameConversation(c.conversation_id, t.trim());
                  }}
                  onDelete={() => {
                    setChatMenu(null);
                    deleteConversation(c.conversation_id);
                  }}
                />
              ))}
            </div>
          </div>
        ))}
      </div>

      {/* Sidebar Footer */}
      <div className="rounded-lg border-t border-white/10 p-1.5">
        {conversations.length > 0 && (
          <button
            onClick={() => {
              clearAllConversations();
              onClose?.();
            }}
            className="flex w-full items-center gap-2.5 rounded-md px-2.5 py-1.5 text-[13px] text-neutral-300 hover:bg-white/10 hover:text-red-400"
          >
            <Trash2 size={14} />
            <span>Clear conversations</span>
          </button>
        )}
        <button className="flex w-full items-center gap-2.5 rounded-md px-2.5 py-1.5 text-[13px] text-neutral-300 hover:bg-white/10">
          <Settings size={15} />
          <span>Settings</span>
        </button>
        <button className="flex w-full items-center gap-2.5 rounded-md px-2.5 py-1.5 text-[13px] text-neutral-300 hover:bg-white/10">
          <HelpCircle size={15} />
          <span>Help</span>
        </button>

        {/* User */}
        <div className="relative mt-1 border-t border-white/10 pt-1">
          <button
            onClick={() => setUserMenuOpen(!userMenuOpen)}
            className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-1.5 transition hover:bg-white/10"
          >
            <img
              src={migptLogo}
              alt="MiGPT"
              className="h-7 w-7 shrink-0 rounded-full object-cover"
            />
            <div className="min-w-0 flex-1 text-left">
              <p
                className="truncate text-[13px] leading-tight font-bold text-white"
                style={{ fontFamily: '"Baloo 2", system-ui, sans-serif', letterSpacing: "0.02em" }}
              >
                MiGPT
              </p>
              <p
                className="truncate text-[12px] leading-none text-neutral-400"
                style={{ fontFamily: '"Pacifico", cursive' }}
              >
                by&nbsp;GenAIWithAM
              </p>
            </div>
            <MoreHorizontal size={16} className="shrink-0 text-neutral-400" />
          </button>

          {userMenuOpen && (
            <div className="absolute -bottom-4 left-2 right-2 rounded-lg border border-white/10 bg-[#1c1c1c] p-1 shadow-lg">
              <button className="flex w-full items-center gap-2.5 rounded-md px-2.5 py-1.5 text-xs text-neutral-200 hover:bg-white/10">
                <User size={14} />
                Profile
              </button>
              <button
                onClick={() => setUserMenuOpen(false)}
                className="flex w-full items-center gap-2.5 rounded-md px-2.5 py-1.5 text-xs text-neutral-200 hover:bg-white/10"
              >
                <Settings size={14} />
                Settings
              </button>
              <button className="flex w-full items-center gap-2.5 rounded-md px-2.5 py-1.5 text-xs text-red-400 hover:bg-red-500/10">
                <LogOut size={14} />
                Log out
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export function Sidebar({ open, onClose }: { open?: boolean; onClose?: () => void }) {
  return (
    <>
      {/* Desktop static — in flow on lg+ */}
      <aside
        className={`hidden w-[270px] shrink-0 flex-col border-r border-white/10 bg-black ${
          open ? "lg:flex" : ""
        }`}
      >
        <SidebarContent onClose={onClose} />
      </aside>

      {/* Mobile drawer */}
      <aside
        className={`fixed inset-y-0 left-0 z-50 flex w-[270px] max-w-[85vw] flex-col border-r border-white/10 bg-black transition-transform duration-200 ease-out lg:hidden ${
          open ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <SidebarContent onClose={onClose} />
      </aside>

      {/* Mobile backdrop */}
      {open && (
        <div
          className="fixed inset-0 z-40 bg-black/50 lg:hidden"
          onClick={onClose}
          aria-hidden
        />
      )}
    </>
  );
}