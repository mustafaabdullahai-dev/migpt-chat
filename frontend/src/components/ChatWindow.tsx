import { useEffect, useRef } from "react";
import { useChat } from "../store/useChat";
import { Message } from "./Message";
import { MiGPTComposer } from "./MiGPTComposer";
import { Markdown } from "../utils/markdown";
import { SourceCard } from "./SourceCard";
import { ErrorBoundary } from "./ErrorBoundary";
import { ResearchActivity } from "./ResearchActivity";
import migptLogo from "../assets/migpt.png";

export function ChatWindow() {
  const { currentId, messages, sending, streamingText, streamingStatus, streamingSources, activity } =
    useChat();
  const scrollRef = useRef<HTMLDivElement>(null);

  const list = currentId ? messages[currentId] ?? [] : [];

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [list.length, streamingText, streamingSources.length, sending]);

  if (!currentId && list.length === 0) {
    return (
      <section className="relative flex min-h-0 flex-1 flex-col">
        <div className="flex flex-1 flex-col items-center justify-center overflow-y-auto px-4 pb-4">
          <div className="flex w-full max-w-5xl flex-col items-center">
            <img
              src={migptLogo}
              alt="MiGPT"
              className="logo-outline mb-5 h-14 w-14 rounded-2xl object-cover shadow-sm"
            />
            <h1 className="text-2xl font-semibold tracking-tight text-primary">
              What can I help with?
            </h1>
            <p className="mt-2 text-center text-sm text-secondary">
              Search the web, analyze information, write code, or build something with MiGPT.
            </p>
          </div>
        </div>
        <MiGPTComposer />
      </section>
    );
  }

  return (
    <section className="relative flex min-h-0 flex-1 flex-col">
      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-5xl flex-col gap-6 px-4 py-8">
          {list.map((m) => (
            <Message key={m.message_id} msg={m} />
          ))}

          {sending && (
            <div className="flex gap-4">
              <img src={migptLogo} alt="MiGPT" className="logo-outline mt-1 h-6 w-6 shrink-0 rounded-lg object-cover" />
              <div className="min-w-0 flex-1">
                <ErrorBoundary>
                  {!streamingText && (
                    <div>
                      {activity.length > 0 ? (
                        <ResearchActivity phases={activity} />
                      ) : (
                        <div className="mb-3 flex items-center gap-2.5 text-sm text-muted">
                          <span className="flex gap-0.5">
                            <span className="pulse-dot h-1 w-1 rounded-full" />
                            <span className="pulse-dot h-1 w-1 rounded-full" />
                            <span className="pulse-dot h-1 w-1 rounded-full" />
                          </span>
                          <span>{streamingStatus || "Thinking…"}</span>
                        </div>
                      )}
                      {streamingStatus && activity.length > 0 && (
                        <div className="mb-3 text-xs text-muted">{streamingStatus}</div>
                      )}
                    </div>
                  )}
                  {streamingSources.length > 0 && (
                    <div className="mb-3 flex gap-2.5 overflow-x-auto pb-1">
                      {streamingSources.map((s, i) => (
                        <SourceCard key={s.source_id || i} source={s} index={i} />
                      ))}
                    </div>
                  )}
                  {streamingText && (
                    <div className="stream-cursor rounded-2xl border border-line bg-panel px-4 py-3">
                      <Markdown content={streamingText} />
                    </div>
                  )}
                </ErrorBoundary>
              </div>
            </div>
          )}
        </div>
      </div>
      <MiGPTComposer />
    </section>
  );
}