import type { ResearchPhase } from "../types";

/**
 * Compact high-level research status. Shows aggregate phases only — never
 * individual tool calls or internal reasoning.
 *
 *   🔎 Searching the web …
 *   ✓ 6 sources found
 *   ✓ 4 sources analyzed
 *   🧠 Synthesizing findings …
 */
export function ResearchActivity({ phases }: { phases: ResearchPhase[] }) {
  if (!phases || phases.length === 0) return null;

  return (
    <div className="mb-3 flex flex-col gap-1 text-[13px]">
      {phases.map((p) => {
        const count = p.count ?? 0;
        if (p.status === "done") {
          const label =
            p.id === "searching"
              ? count > 0
                ? `${count} sources found`
                : "Sources found"
              : p.id === "reading"
                ? count > 0
                  ? `${count} sources analyzed`
                  : "Sources analyzed"
                : null;
          if (!label) return null;
          return (
            <div key={p.id} className="flex items-center gap-2 text-success">
              <span aria-hidden>✔</span>
              <span>{label}</span>
            </div>
          );
        }
        const icon =
          p.id === "searching" ? "🔎" : p.id === "reading" ? "🌐" : "🧠";
        return (
          <div key={p.id} className="flex items-center gap-2 text-secondary">
            <span aria-hidden>{icon}</span>
            <span>{p.label}</span>
            <span className="flex gap-0.5">
              <span className="pulse-dot h-1 w-1 rounded-full" />
              <span className="pulse-dot h-1 w-1 rounded-full" />
              <span className="pulse-dot h-1 w-1 rounded-full" />
            </span>
          </div>
        );
      })}
    </div>
  );
}