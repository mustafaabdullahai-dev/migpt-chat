import { Menu } from "lucide-react";
import migptLogo from "../assets/migpt.png";

export function Header({
  sidebarOpen,
  onToggleSidebar,
}: {
  sidebarOpen: boolean;
  onToggleSidebar: () => void;
}) {
  return (
    <header className="relative z-[60] flex h-14 w-full shrink-0 items-center justify-between px-3">
      <div className="flex items-center gap-1">
        {!sidebarOpen && (
          <button
            onClick={onToggleSidebar}
            className="flex h-9 w-9 items-center justify-center rounded-lg text-secondary hover:bg-card"
            title="Open sidebar"
          >
            <Menu size={20} />
          </button>
        )}
        <button
          className="flex items-center gap-2 rounded-lg px-2 py-1.5 hover:bg-card"
          title="MiGPT"
        >
          {!sidebarOpen && (
            <img src={migptLogo} alt="MiGPT" className="logo-outline h-7 w-7 rounded-lg object-cover" />
          )}
          <span className="text-[17px] font-semibold tracking-tight text-primary">MiGPT</span>
        </button>
      </div>
    </header>
  );
}