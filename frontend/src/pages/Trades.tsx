import React, { useEffect, useState } from "react";
import { getTrades, exportTradesCsv } from "../api";

export default function Trades() {
  const [trades, setTrades] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [filters, setFilters] = useState({ strategy_type: "", status: "" });

  useEffect(() => {
    async function load() {
      setLoading(true);
      try {
        const params: any = { limit: 100 };
        if (filters.strategy_type) params.strategy_type = filters.strategy_type;
        if (filters.status) params.status = filters.status;
        const res = await getTrades(params);
        setTrades(res.data);
      } catch { /* ignore */ }
      setLoading(false);
    }
    load();
  }, [filters]);

  const handleExport = async () => {
    try {
      const res = await exportTradesCsv();
      const url = window.URL.createObjectURL(new Blob([res.data]));
      const a = document.createElement("a");
      a.href = url;
      a.download = "trades.csv";
      a.click();
    } catch { /* ignore */ }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-bold">Trade History</h2>
        <button
          onClick={handleExport}
          className="px-4 py-2 bg-gray-800 hover:bg-gray-700 text-sm rounded-lg text-gray-300"
        >
          Export CSV
        </button>
      </div>

      {/* Filters */}
      <div className="flex gap-3">
        <select
          value={filters.strategy_type}
          onChange={(e) => setFilters((f) => ({ ...f, strategy_type: e.target.value }))}
          className="px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-300"
        >
          <option value="">All Strategies</option>
          <option value="binary">Binary</option>
          <option value="multi_outcome">Multi-Outcome</option>
        </select>
        <select
          value={filters.status}
          onChange={(e) => setFilters((f) => ({ ...f, status: e.target.value }))}
          className="px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-300"
        >
          <option value="">All Statuses</option>
          <option value="filled">Filled</option>
          <option value="submitted">Submitted</option>
          <option value="cancelled">Cancelled</option>
          <option value="failed">Failed</option>
        </select>
      </div>

      {/* Table */}
      <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-gray-500 text-xs uppercase border-b border-gray-800">
                <th className="text-left px-4 py-2">Timestamp</th>
                <th className="text-left px-4 py-2">Market</th>
                <th className="text-left px-4 py-2">Strategy</th>
                <th className="text-left px-4 py-2">Side</th>
                <th className="text-right px-4 py-2">Size</th>
                <th className="text-right px-4 py-2">Fill Price</th>
                <th className="text-right px-4 py-2">Fees</th>
                <th className="text-right px-4 py-2">P&L</th>
                <th className="text-center px-4 py-2">Status</th>
                <th className="text-center px-4 py-2">Mode</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr><td colSpan={10} className="text-center py-8 text-gray-600">Loading...</td></tr>
              ) : trades.length === 0 ? (
                <tr><td colSpan={10} className="text-center py-8 text-gray-600">No trades yet</td></tr>
              ) : (
                trades.map((t) => (
                  <tr key={t.id} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                    <td className="px-4 py-3 text-gray-500 text-xs">
                      {new Date(t.executed_at).toLocaleString()}
                    </td>
                    <td className="px-4 py-3 text-gray-300 max-w-xs truncate">{t.market_id}</td>
                    <td className="px-4 py-3">
                      <span className="text-xs px-2 py-0.5 rounded bg-gray-800 text-gray-400">
                        {t.strategy_type}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-gray-300">{t.side}</td>
                    <td className="px-4 py-3 text-right font-mono">${t.size_usdc?.toFixed(2)}</td>
                    <td className="px-4 py-3 text-right font-mono text-gray-400">
                      {t.fill_price?.toFixed(4) || "—"}
                    </td>
                    <td className="px-4 py-3 text-right text-gray-500">
                      ${t.fees_paid?.toFixed(4) || "0.00"}
                    </td>
                    <td className={`px-4 py-3 text-right font-medium ${
                      (t.profit_loss || 0) >= 0 ? "text-green-400" : "text-red-400"
                    }`}>
                      ${t.profit_loss?.toFixed(4) || "0.00"}
                    </td>
                    <td className="px-4 py-3 text-center">
                      <span className={`text-xs px-2 py-0.5 rounded ${
                        t.status === "filled" ? "bg-green-900/30 text-green-400" :
                        t.status === "submitted" ? "bg-blue-900/30 text-blue-400" :
                        t.status === "failed" ? "bg-red-900/30 text-red-400" :
                        "bg-gray-800 text-gray-500"
                      }`}>
                        {t.status}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-center">
                      {t.is_paper && (
                        <span className="text-xs bg-yellow-900/30 text-yellow-400 px-2 py-0.5 rounded">
                          PAPER
                        </span>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
