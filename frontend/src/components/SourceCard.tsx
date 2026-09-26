import { ExternalLink } from "lucide-react";
import type { Source } from "../types";

export function SourceCard({ source, index }: { source: Source; index: number }) {
  let host = "";
  try {
    host = new URL(source.url).hostname.replace(/^www\./, "");
  } catch {
    host = source.url;
  }
  return (
    <a
      href={source.url}
      target="_blank"
      rel="noopener noreferrer"
      className="flex w-56 shrink-0 flex-col gap-1 rounded-xl border border-line bg-card p-3 transition-colors hover:bg-card-2"
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-[10px] font-semibold text-muted">{index + 1}</span>
        <ExternalLink size={12} className="text-muted" />
      </div>
      <p className="line-clamp-2 text-xs font-medium text-primary">{source.title}</p>
      <p className="line-clamp-2 text-[11px] leading-snug text-muted">
        {source.snippet || source.url}
      </p>
      <span className="mt-1 truncate text-[10px] text-muted">{host}</span>
    </a>
  );
}