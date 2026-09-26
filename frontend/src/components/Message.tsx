import { useState } from "react";
import {
  ThumbsUp,
  ThumbsDown,
  RotateCcw,
  Copy,
  Check,
  Link2,
  ChevronDown,
  FileText,
} from "lucide-react";
import type { ChatMessage, Source } from "../types";
import { Markdown } from "../utils/markdown";
import { ToolActivity } from "./ToolActivity";
import { ErrorBoundary } from "./ErrorBoundary";
import { useChat } from "../store/useChat";
import migptLogo from "../assets/migpt.png";

function SourceLinkIcon({ sources }: { sources: Source[] }) {
  const [open, setOpen] = useState(false);
  if (sources.length === 0) return null;
  return (
    <div className="mt-3">
      <button
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-1.5 rounded-full border border-line bg-card px-2.5 py-1 text-xs text-secondary transition-colors hover:border-accent hover:text-primary"
        title="Show sources"
      >
        <Link2 size={13} />
        <span>{sources.length > 1 ? `${sources.length} sources` : "1 source"}</span>
        <ChevronDown
          size={12}
          className={`text-muted transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>
      {open && (
        <ul className="mt-2 space-y-1.5">
          {sources.map((s, i) => {
            const n = typeof s.citation_index === "number" ? s.citation_index : i + 1;
            const label = s.title || s.url || `Source ${n}`;
            return (
              <li key={s.source_id || i} className="text-sm leading-snug">
                <a
                  href={s.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-secondary hover:text-accent hover:underline"
                >
                  <span className="mr-1 font-medium text-muted">[{n}]</span>
                  {label}
                </a>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function AssistantMessage({ msg }: { msg: ChatMessage }) {
  const rate = useChat((s) => s.rate);
  const send = useChat((s) => s.send);
  const [copied, setCopied] = useState(false);
  const [rating, setRating] = useState<"up" | "down" | null>(null);

  const content = String(msg.content ?? "");
  const tool_runs = Array.isArray(msg.tool_runs) ? msg.tool_runs : [];
  const sources = Array.isArray(msg.sources) ? msg.sources : [];

  const copy = async () => {
    await navigator.clipboard.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 1200);
  };

  return (
    <div className="group flex gap-4">
      <img
        src={migptLogo}
        alt="MiGPT"
        className="logo-outline mt-1 h-6 w-6 shrink-0 rounded-lg object-cover"
      />
      <div className="min-w-0 flex-1">
        {tool_runs.length > 0 && <ToolActivity runs={tool_runs} />}
        {content ? (
          <ErrorBoundary>
            <div className="min-w-0 rounded-2xl border border-line bg-panel px-4 py-3 text-primary">
              <Markdown content={content} />
            </div>
          </ErrorBoundary>
        ) : (
          <p className="text-sm text-muted">Thinking…</p>
        )}
        {sources.length > 0 && <SourceLinkIcon sources={sources} />}
        {Array.isArray(msg.followups) && msg.followups.length > 0 && (
          <div className="mt-4">
            <p className="mb-2 text-xs font-medium text-secondary">Follow-up</p>
            <div className="flex flex-wrap gap-2">
              {msg.followups.map((f, i) => (
                <button
                  key={i}
                  onClick={() => send(f)}
                  className="rounded-full border border-line bg-card px-3 py-1.5 text-left text-[13px] text-primary hover:border-accent hover:text-accent"
                >
                  {f}
                </button>
              ))}
            </div>
          </div>
        )}
        <div className="mt-2 flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100">
          <button onClick={copy} className="rounded p-1.5 text-muted hover:bg-card hover:text-primary" title="Copy">
            {copied ? <Check size={14} /> : <Copy size={14} />}
          </button>
          <button
            onClick={() => setRating((prev) => (prev === "up" ? null : "up"))}
            className={`rounded p-1.5 text-muted hover:bg-card hover:text-primary ${rating === "up" ? "text-primary" : ""}`}
            title="Good response"
          >
            <ThumbsUp size={14} />
          </button>
          <button
            onClick={() => setRating((prev) => (prev === "down" ? null : "down"))}
            className={`rounded p-1.5 text-muted hover:bg-card hover:text-danger ${rating === "down" ? "text-danger" : ""}`}
            title="Bad response"
          >
            <ThumbsDown size={14} />
          </button>
          <button
            onClick={() => send(content)}
            className="rounded p-1.5 text-muted hover:bg-card hover:text-primary"
            title="Re-run query"
          >
            <RotateCcw size={14} />
          </button>
        </div>
        {rating && (
          <button
            onClick={() => rate(msg.conversation_id, msg.message_id, rating)}
            className="mt-1 text-[11px] text-primary hover:underline"
          >
            Submit feedback
          </button>
        )}
      </div>
    </div>
  );
}

export function Message({ msg }: { msg: ChatMessage }) {
  if (msg.role === "user") {
    const attachments = Array.isArray(msg.attachments) ? msg.attachments : [];
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%]">
          {attachments.length > 0 && (
            <div className="mb-1.5 flex justify-end">
              <div className="flex flex-wrap justify-end gap-1.5">
                {attachments.map((a, i) => (
                  <span
                    key={`${a.document_id}-${i}`}
                    className="inline-flex max-w-full items-center gap-1.5 rounded-lg border border-line bg-card px-2 py-1 text-xs text-secondary"
                  >
                    <FileText size={12} className="shrink-0 text-muted" />
                    <span className="truncate">{a.filename || a.document_id}</span>
                  </span>
                ))}
              </div>
            </div>
          )}
          <div className="whitespace-pre-wrap rounded-2xl bg-card px-4 py-2.5 text-[15px] leading-relaxed text-primary">
            {String(msg.content ?? "")}
          </div>
        </div>
      </div>
    );
  }
  return (
    <ErrorBoundary>
      <AssistantMessage msg={msg} />
    </ErrorBoundary>
  );
}