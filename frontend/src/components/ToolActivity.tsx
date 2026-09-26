import {
  CheckCircle2,
  Globe,
  Calculator,
  FileText,
  ArrowRight,
  Search,
  BookOpen,
  XCircle,
  Loader2,
  type LucideIcon,
} from "lucide-react";
import type { ToolRun } from "../types";

const ICONS: Record<string, LucideIcon> = {
  web_search: Globe,
  fetch_webpage: Globe,
  wikipedia: BookOpen,
  arxiv: BookOpen,
  calculator: Calculator,
  document_search: FileText,
  document_upload: FileText,
  classify: Search,
  extract_expression: Calculator,
  think: Loader2,
};

function shorten(args: unknown) {
  if (typeof args !== "string" || !args || args.length <= 90) return "";
  return args.slice(0, 90) + "…";
}

export function ToolActivity({
  runs,
  live,
}: {
  runs: ToolRun[];
  live?: boolean;
}) {
  const safe = (runs ?? []).filter((r) => {
    const t = String(r.tool ?? "").toLowerCase();
    return t !== "" && t !== "status" && t !== "heartbeat";
  });
  if (safe.length === 0 && !live) return null;

  return (
    <div className="mb-3 flex flex-col gap-1.5 border-l-2 border-line pl-3">
      {safe.map((r, i) => {
        const name = String(r.tool ?? "status").replace(/_/g, " ");
        const Icon = ICONS[String(r.tool ?? "")] ?? Loader2;
        const args = shorten(r.arguments);
        return (
          <div key={`${r.tool ?? "tool"}-${i}`} className="flex items-center gap-2 text-xs text-secondary">
            <Icon size={13} className={r.status === "error" ? "text-danger" : "text-secondary"} />
            <span className="font-medium capitalize text-primary">{name}</span>
            {args && <span className="truncate text-muted">{args}</span>}
            {r.status === "running" && (
              <span className="flex gap-0.5">
                <span className="pulse-dot h-1 w-1 rounded-full" />
                <span className="pulse-dot h-1 w-1 rounded-full" />
                <span className="pulse-dot h-1 w-1 rounded-full" />
              </span>
            )}
            {r.status === "success" && <CheckCircle2 size={13} className="text-success" />}
            {r.status === "error" && <XCircle size={13} className="text-danger" />}
            {r.duration_ms ? (
              <span className="ml-auto shrink-0 text-[10px] text-muted">
                {r.duration_ms < 1000 ? `${r.duration_ms}ms` : `${(r.duration_ms / 1000).toFixed(1)}s`}
                {live && <ArrowRight size={11} className="ml-1 inline text-muted" />}
              </span>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}