import { memo, useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import type { Components } from "react-markdown";
import { ErrorBoundary } from "../components/ErrorBoundary";

const components: Components = {
  a: ({ node, children, href, ...props }) => (
    <a href={href} target="_blank" rel="noopener noreferrer" {...props}>
      {children}
    </a>
  ),
};

const URL_RE = /(^|[\s(])(https?:\/\/[^\s<>()[\]"'`]+)/gi;

function linkify(content: string): string {
  // Only transform text that is NOT inside code spans/fences so `const a = [1,2]` stays intact.
  const segments = content.split(/(```[\s\S]*?```|`[^`\n]*`)/g);
  return segments
    .map((seg, i) => {
      if (i % 2 === 1) return seg; // code — untouched

      // bare URLs -> clickable links
      seg = seg.replace(URL_RE, (_match, lead, url) => {
        if (typeof url !== "string") return _match;
        const clean = url.replace(/[.,;:!?)\]]+$/, "");
        return `${lead}[${clean}](${clean})`;
      });

      return seg;
    })
    .join("");
}

export const Markdown = memo(function Markdown({
  content,
}: {
  content: string;
}) {
  const safeContent = useMemo(
    () => String(content ?? ""),
    [content],
  );
  const linked = useMemo(() => linkify(safeContent), [safeContent]);
  return (
    <ErrorBoundary
      fallback={
        <div className="markdown-body whitespace-pre-wrap rounded-xl border border-line bg-card p-3 text-sm leading-relaxed">
          {String(content ?? "")}
        </div>
      }
    >
      <div className="markdown-body">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          rehypePlugins={[[rehypeHighlight, { detect: false }]]}
          components={components}
        >
          {linked}
        </ReactMarkdown>
      </div>
    </ErrorBoundary>
  );
});