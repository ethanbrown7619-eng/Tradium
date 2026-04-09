import React from "react";
import { Link, useLocation } from "react-router-dom";
import { useStore } from "../store";
import { useWebSocket } from "../hooks/useWebSocket";
import { activateKillSwitch } from "../api";

const NAV_ITEMS = [
  { path: "/", label: "Dashboard", icon: "grid" },
  { path: "/scanner", label: "Scanner", icon: "radio" },
  { path: "/opportunities", label: "Opportunities", icon: "zap" },
  { path: "/trades", label: "Trades", icon: "list" },
  { path: "/performance", label: "Performance", icon: "trending-up" },
  { path: "/markets", label: "Markets", icon: "globe" },
  { path: "/config", label: "Config", icon: "settings" },
];

export default function Layout({ children }: { children: React.ReactNode }) {
  useWebSocket();
  const location = useLocation();
  const { user, config, wsConnected, logout } = useStore();

  const handleKillSwitch = async () => {
    if (window.confirm("KILL SWITCH: This will immediately stop the bot and cancel all pending orders. Continue?")) {
      try {
        await activateKillSwitch(true);
        window.location.reload();
      } catch {
        alert("Failed to activate kill switch");
      }
    }
  };

  return (
    <div className="flex h-screen bg-gray-950">
      {/* Sidebar */}
      <aside className="w-64 bg-gray-900 border-r border-gray-800 flex flex-col">
        <div className="p-4 border-b border-gray-800">
          <h1 className="text-xl font-bold text-brand-500">Tradium</h1>
          <p className="text-xs text-gray-500 mt-1">Polymarket Arbitrage Bot</p>
        </div>

        <nav className="flex-1 p-3 space-y-1">
          {NAV_ITEMS.map((item) => (
            <Link
              key={item.path}
              to={item.path}
              className={`flex items-center px-3 py-2 rounded-lg text-sm transition-colors ${
                location.pathname === item.path
                  ? "bg-brand-600/20 text-brand-500"
                  : "text-gray-400 hover:text-gray-200 hover:bg-gray-800"
              }`}
            >
              {item.label}
            </Link>
          ))}
        </nav>

        <div className="p-3 border-t border-gray-800 space-y-2">
          {/* Kill Switch */}
          <button
            onClick={handleKillSwitch}
            className="w-full py-2 px-4 bg-red-600 hover:bg-red-700 text-white font-bold rounded-lg text-sm transition-colors"
          >
            KILL SWITCH
          </button>

          {/* Status indicators */}
          <div className="flex items-center justify-between text-xs text-gray-500 px-1">
            <span className="flex items-center gap-1">
              <span className={`w-2 h-2 rounded-full ${wsConnected ? "bg-green-500" : "bg-red-500"}`} />
              {wsConnected ? "Connected" : "Disconnected"}
            </span>
            {config?.paper_trading && (
              <span className="bg-yellow-600/20 text-yellow-500 px-2 py-0.5 rounded text-xs font-medium">
                PAPER
              </span>
            )}
          </div>

          {/* User */}
          <div className="flex items-center justify-between text-xs px-1">
            <span className="text-gray-400 truncate">{user?.email}</span>
            <button onClick={logout} className="text-gray-500 hover:text-gray-300">
              Logout
            </button>
          </div>
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-auto p-6">{children}</main>
    </div>
  );
}
