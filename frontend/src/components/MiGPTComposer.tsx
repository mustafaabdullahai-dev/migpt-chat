import { useRef, useState, type KeyboardEvent } from "react";
import {
  ArrowUp,
  Square,
  ChevronDown,
  Globe,
  Paperclip,
  Plus,
  Sparkles,
  X,
  FileText,
  FileSpreadsheet,
} from "lucide-react";
import { useChat } from "../store/useChat";
import type { Mode } from "../types";

const MODELS: { id: Mode; label: string; tagline: string }[] = [
  { id: "no_web", label: "MiGPT Fast", tagline: "Fast responses" },
  { id: "auto", label: "MiGPT Reasoning", tagline: "Complex problems" },
  { id: "deep", label: "MiGPT Research", tagline: "Web research and analysis" },
];

const UPLOAD_EXTENSIONS = [".txt", ".md", ".pdf", ".docx", ".csv"];

const fileIcon = (name: string) =>
  name.toLowerCase().endsWith(".csv") || name.toLowerCase().endsWith(".md") ? (
    <FileSpreadsheet size={12} className="shrink-0 text-muted" />
  ) : (
    <FileText size={12} className="shrink-0 text-muted" />
  );

export function MiGPTComposer() {
  const { sending, send, stop, documents, uploadDocument, deleteDocument, mode, setMode } = useChat();
  const [message, setMessage] = useState("");
  const [showTools, setShowTools] = useState(false);
  const [showModels, setShowModels] = useState(false);
  const [pending, setPending] = useState<{ key: string; name: string }[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const searchEnabled = mode !== "no_web";
  const currentModel =
    MODELS.find((m) => m.id === mode) ?? (mode === "web" ? MODELS[2] : MODELS[1]);

  const submit = () => {
    const value = message.trim();
    if (!value || sending || uploading) return;
    send(value, documents.map((d) => d.document_id));
    setMessage("");
    if (textareaRef.current) textareaRef.current.style.height = "auto";
  };

  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setMessage(e.target.value);
    e.target.style.height = "auto";
    e.target.style.height = Math.min(e.target.scrollHeight, 180) + "px";
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  };

  const onPick = async (f: File | undefined) => {
    if (!f) return;
    const name = f.name.toLowerCase();
    const ext = "." + name.split(".").pop();
    if (!UPLOAD_EXTENSIONS.includes(ext)) {
      setUploadError("Only PDF, DOCX, TXT, MD, CSV are supported.");
      return;
    }
    setUploading(true);
    setUploadError(null);
    const key = crypto.randomUUID();
    setPending((p) => [...p, { key, name: f.name }]);
    try {
      await uploadDocument(f);
      setPending((p) => p.filter((x) => x.key !== key));
    } catch {
      setPending((p) => p.filter((x) => x.key !== key));
      setUploadError("Upload failed. Please try again.");
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="w-full px-4 pb-5">
      <div className="mx-auto w-full max-w-5xl">
        {uploadError && <p className="mb-2 text-center text-xs text-danger">{uploadError}</p>}

        {(pending.length > 0 || documents.length > 0) && (
          <div className="mb-2 flex flex-wrap justify-center gap-2">
            {pending.map((p) => (
              <span
                key={p.key}
                className="flex items-center gap-1.5 rounded-lg border border-line bg-card px-2.5 py-1 text-xs text-secondary"
              >
                {fileIcon(p.name)}
                <span className="max-w-40 truncate">{p.name}</span>
                <button onClick={() => setPending((prev) => prev.filter((x) => x.key !== p.key))}>
                  <X size={12} className="text-muted hover:text-danger" />
                </button>
              </span>
            ))}
            {documents.map((d) => (
              <span
                key={d.document_id}
                className="flex items-center gap-1.5 rounded-lg border border-line bg-card px-2.5 py-1 text-xs text-secondary"
              >
                {fileIcon(d.filename ?? "")}
                <span className="max-w-40 truncate">{d.filename ?? d.document_id}</span>
                <button onClick={() => deleteDocument(d.document_id)}>
                  <X size={12} className="text-muted hover:text-danger" />
                </button>
              </span>
            ))}
          </div>
        )}

        {/* SEARCH / MESSAGE BOX — single compact row */}
        <div className="relative overflow-visible rounded-[28px] border border-black/[0.12] bg-bg shadow-[0_2px_14px_rgba(0,0,0,0.06)] transition-all focus-within:border-black/[0.20] focus-within:shadow-[0_3px_18px_rgba(0,0,0,0.08)] dark:border-white/15 dark:bg-panel dark:focus-within:border-white/30">

          {/* TEXT INPUT */}
          <textarea
            ref={textareaRef}
            value={message}
            onChange={handleInput}
            onKeyDown={handleKeyDown}
            rows={1}
            placeholder="Ask MiGPT anything"
            className="block max-h-[100px] min-h-[44px] w-full resize-none overflow-y-auto bg-transparent px-5 pb-1 pt-3 text-sm leading-5 text-primary outline-none placeholder:text-muted dark:text-white dark:placeholder:text-neutral-500"
          />

          {/* SINGLE-ROW TOOLBAR */}
          <div className="flex items-center justify-between px-2.5 pb-2">
            <div className="flex min-w-0 flex-1 items-center gap-1">
              {/* PLUS / ATTACHMENT */}
              <div className="relative">
                <button
                  onClick={() => setShowTools(!showTools)}
                  className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-secondary transition hover:bg-card dark:text-neutral-400 dark:hover:bg-white/10"
                  title="Add tools"
                >
                  <Plus size={18} />
                </button>

                {showTools && (
                  <div className="absolute bottom-10 left-0 z-50 w-52 rounded-2xl border border-line bg-bg p-1.5 shadow-[0_8px_30px_rgba(0,0,0,0.12)] dark:border-white/10 dark:bg-[#1c1c1c]">
                    <button
                      onClick={() => {
                        setShowTools(false);
                        fileRef.current?.click();
                      }}
                      className="flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-sm text-primary hover:bg-card dark:text-neutral-200 dark:hover:bg-white/10"
                    >
                      <Paperclip size={17} />
                      Upload file
                    </button>
                    <button
                      onClick={() => {
                        setMode("deep");
                        setShowTools(false);
                      }}
                      className="flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-sm text-primary hover:bg-card dark:text-neutral-200 dark:hover:bg-white/10"
                    >
                      <Sparkles size={17} />
                      Analyze
                    </button>
                    <button
                      onClick={() => {
                        setMode("web");
                        setShowTools(false);
                      }}
                      className="flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-sm text-primary hover:bg-card dark:text-neutral-200 dark:hover:bg-white/10"
                    >
                      <Globe size={17} />
                      Search the web
                    </button>
                  </div>
                )}
              </div>

              {/* WEB SEARCH CHIP (compact inline pill) */}
              {searchEnabled && (
                <span className="flex h-8 shrink-0 items-center gap-1.5 rounded-full bg-card px-2.5 text-xs font-medium text-secondary dark:bg-white/10 dark:text-neutral-200">
                  <Globe size={13} />
                  <span className="hidden sm:inline">Web</span>
                  <button
                    onClick={() => setMode("no_web")}
                    className="rounded-full hover:opacity-70"
                    title="Disable web search"
                  >
                    <X size={12} />
                  </button>
                </span>
              )}

              {/* SEARCH BUTTON */}
              <button
                onClick={() => setMode(searchEnabled ? "no_web" : "auto")}
                className={`flex h-8 shrink-0 items-center gap-1.5 rounded-full px-2.5 text-xs transition ${
                  searchEnabled
                    ? "bg-card text-primary dark:bg-white/10 dark:text-white"
                    : "text-secondary hover:bg-card dark:text-neutral-400 dark:hover:bg-white/10"
                }`}
                title="Web search is off — click to turn it on"
              >
                <Globe size={15} />
                <span className="hidden md:inline">Search</span>
              </button>

              {/* MODEL PICKER */}
              <div className="relative">
                <button
                  onClick={() => setShowModels(!showModels)}
                  className="flex h-8 shrink-0 items-center gap-1 rounded-full px-2.5 text-xs text-secondary transition hover:bg-card dark:text-neutral-400 dark:hover:bg-white/10"
                  title={currentModel.tagline}
                >
                  <span className="truncate">{currentModel.label}</span>
                  <ChevronDown size={13} />
                </button>

                {showModels && (
                  <div className="absolute bottom-10 left-0 z-50 w-56 rounded-2xl border border-line bg-bg p-1.5 shadow-[0_8px_30px_rgba(0,0,0,0.12)] dark:border-white/10 dark:bg-[#1c1c1c]">
                    {MODELS.map((m) => (
                      <button
                        key={m.id}
                        onClick={() => {
                          setMode(m.id);
                          setShowModels(false);
                        }}
                        className="flex w-full flex-col items-start rounded-xl px-3 py-2.5 text-left hover:bg-card dark:hover:bg-white/10"
                      >
                        <span className="text-sm font-medium text-primary dark:text-neutral-100">
                          {m.label}
                        </span>
                        <span className="text-xs text-muted dark:text-neutral-500">{m.tagline}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </div>

            {/* SEND / STOP */}
            {sending ? (
              <button
                onClick={stop}
                className="ml-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary text-bg transition-all hover:opacity-80 dark:bg-white dark:text-black"
                title="Stop generating"
              >
                <Square size={14} fill="currentColor" />
              </button>
            ) : (
              <button
                onClick={submit}
                disabled={!message.trim() || uploading}
                className="ml-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary text-bg transition-all hover:opacity-80 disabled:cursor-not-allowed disabled:bg-card-2 disabled:text-muted dark:bg-white dark:text-black dark:disabled:bg-white/20 dark:disabled:text-neutral-400"
                title="Send (Enter)"
              >
                <ArrowUp size={16} strokeWidth={2.5} />
              </button>
            )}
          </div>
        </div>

        {/* DISCLAIMER */}
        <p className="mt-2 text-center text-[11px] text-muted">
          MiGPT can make mistakes. Check important information.
        </p>
      </div>

      {/* HIDDEN FILE INPUT */}
      <input
        ref={fileRef}
        type="file"
        accept=".txt,.md,.pdf,.docx,.csv"
        className="hidden"
        onChange={(e) => {
          onPick(e.target.files?.[0]);
          e.target.value = "";
        }}
      />
    </div>
  );
}