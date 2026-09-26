import { useEffect } from "react";
import { Sidebar } from "./components/Sidebar";
import { Header } from "./components/Header";
import { ChatWindow } from "./components/ChatWindow";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { useChat } from "./store/useChat";
import { api } from "./services/api";

export function App() {
  const { theme, setTheme, healthError, sidebarOpen, closeSidebar, toggleSidebar } = useChat();

  useEffect(() => {
    const saved = localStorage.getItem("theme") as "dark" | "light" | null;
    setTheme(saved ?? "dark");
  }, [setTheme]);

  useEffect(() => {
    api.health().then(() => useChat.setState({ healthError: null })).catch(() => {});
  }, []);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
  }, [theme]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && sidebarOpen) closeSidebar();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [sidebarOpen, closeSidebar]);

  return (
    <ErrorBoundary>
      <div className="flex h-full overflow-hidden bg-bg text-primary">
        <Sidebar open={sidebarOpen} onClose={closeSidebar} />
        <section className="flex min-w-0 flex-1 flex-col">
          {healthError && <TopBar tpl2={healthError} clear={() => useChat.setState({ healthError: null })} />}
          <Header
            sidebarOpen={sidebarOpen}
            onToggleSidebar={toggleSidebar}
          />
          <ChatWindow />
        </section>
      </div>
    </ErrorBoundary>
  );
}

function TopBar({ tpl2, clear }: { tpl2: string; clear: () => void }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-line bg-card px-4 py-2 text-xs text-secondary">
      <span>{tpl2}</span>
      <button onClick={clear} className="shrink-0 rounded px-2 py-0.5 text-primary hover:underline">
        Dismiss
      </button>
    </div>
  );
}