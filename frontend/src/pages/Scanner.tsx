import React, { useEffect, useState } from "react";
import { useStore } from "../store";
import { getOpportunities } from "../api";

export default function Scanner() {
  const scannerFeed = useStore((s) => s.scannerFeed);
  const config = useStore((s) => s.config);
  const [liveOpps, setLiveOpps] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function load() {
      try {
        const res = await getOpportunities({ limit: 50 });
        setLiveOpps(res.data);
      } catch { /* ignore */ }
      setLoading(false);
    }
    load();
    const interval = setInterval(load, (config?.scan_interval || 15) * 1000);
    return () => clearInterval(interval);
  }, [config?.scan_interval]);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-bold">Live Scanner</h2>
        <span className="text-xs text-gray-500">
          Refreshes every {config?.scan_interval || 15}s
        </span>
      </div>

      {/* Current Opportunities */}
      <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
        <div className="px-4 py-3 border-b border-gray-800">
          <h3 className="text-sm font-medium text-gray-400">Current Opportunities</h3>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-gray-500 text-xs uppercase border-b border-gray-800">
                <th className="text-left px-4 py-2">Market</th>
                <th className="text-left px-4 py-2">Type</th>
                <th className="text-right px-4 py-2">Prices</th>
                <th className="text-right px-4 py-2">Sum</th>
                <th className="text-right px-4 py-2">Profit %</th>
                <th className="text-right px-4 py-2">Profit $</th>
                <th className="text-right px-4 py-2">Liquidity</th>
                <th className="text-center px-4 py-2">Status</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr><td colSpan={8} className="text-center py-8 text-gray-600">Loading...</td></tr>
              ) : liveOpps.length === 0 ? (
                <tr><td colSpan={8} className="text-center py-8 text-gray-600">No opportunities detected</td></tr>
              ) : (
                liveOpps.map((opp) => (
                  <tr key={opp.id} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                    <td className="px-4 py-3 text-gray-300 max-w-xs truncate">
                      {opp.market_question || opp.market_id}
                    </td>
                    <td className="px-4 py-3">
                      <span className={`text-xs px-2 py-0.5 rounded ${
                        opp.strategy_type === "binary" ? "bg-blue-900/30 text-blue-400" :
                        opp.strategy_type === "multi_outcome" ? "bg-purple-900/30 text-purple-400" :
                        "bg-orange-900/30 text-orange-400"
                      }`}>
                        {opp.strategy_type}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-right text-gray-400 text-xs font-mono">
                      {Object.entries(opp.outcome_prices || {}).map(([k, v]: [string, any]) =>
                        `${k}: ${v.toFixed(3)}`
                      ).join(", ")}
                    </td>
                    <td className="px-4 py-3 text-right font-mono">{opp.price_sum?.toFixed(4)}</td>
                    <td className="px-4 py-3 text-right text-green-400 font-medium">
                      +{opp.estimated_profit_pct?.toFixed(2)}%
                    </td>
                    <td className="px-4 py-3 text-right text-green-400">
                      ${opp.estimated_profit_usdc?.toFixed(2) || "—"}
                    </td>
                    <td className="px-4 py-3 text-right text-gray-400">
                      ${opp.liquidity_depth?.toFixed(0) || "—"}
                    </td>
                    <td className="px-4 py-3 text-center">
                      <span className={`text-xs px-2 py-0.5 rounded ${
                        opp.status === "executed" ? "bg-green-900/30 text-green-400" :
                        opp.status === "queued" ? "bg-blue-900/30 text-blue-400" :
                        opp.status === "pending" ? "bg-yellow-900/30 text-yellow-400" :
                        opp.status === "failed" ? "bg-red-900/30 text-red-400" :
                        "bg-gray-800 text-gray-500"
                      }`}>
                        {opp.status}
                      </span>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Live Feed */}
      <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
        <h3 className="text-sm font-medium text-gray-400 mb-3">Live Feed</h3>
        <div className="space-y-1 max-h-64 overflow-y-auto font-mono text-xs">
          {scannerFeed.length === 0 ? (
            <p className="text-gray-600">Waiting for scanner events...</p>
          ) : (
            scannerFeed.map((event, i) => (
              <div key={i} className="text-gray-500">
                <span className="text-gray-600">{new Date().toLocaleTimeString()}</span>{" "}
                <span className="text-gray-400">{event.type}</span>{" "}
                {event.message || JSON.stringify(event.data || {}).slice(0, 100)}
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
