import React, { useEffect, useState } from "react";
import { getOpportunities, opportunityAction } from "../api";

type Tab = "auto" | "pending" | "expired";

export default function Opportunities() {
  const [tab, setTab] = useState<Tab>("auto");
  const [opps, setOpps] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  const statusMap: Record<Tab, string | undefined> = {
    auto: "queued",
    pending: "flagged",
    expired: "expired",
  };

  useEffect(() => {
    async function load() {
      setLoading(true);
      try {
        const res = await getOpportunities({ status: statusMap[tab], limit: 100 });
        setOpps(res.data);
      } catch { /* ignore */ }
      setLoading(false);
    }
    load();
  }, [tab]);

  const handleAction = async (id: string, action: string) => {
    try {
      await opportunityAction(id, action);
      setOpps((prev) => prev.filter((o) => o.id !== id));
    } catch { /* ignore */ }
  };

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-bold">Opportunities Queue</h2>

      {/* Tabs */}
      <div className="flex gap-2">
        {(["auto", "pending", "expired"] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-2 rounded-lg text-sm transition-colors ${
              tab === t
                ? "bg-brand-600/20 text-brand-500"
                : "text-gray-400 hover:text-gray-200 bg-gray-800"
            }`}
          >
            {t === "auto" ? "Auto-Executing" : t === "pending" ? "Pending Approval" : "Expired"}
          </button>
        ))}
      </div>

      {/* Table */}
      <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-gray-500 text-xs uppercase border-b border-gray-800">
                <th className="text-left px-4 py-2">Market</th>
                <th className="text-left px-4 py-2">Strategy</th>
                <th className="text-right px-4 py-2">Prices</th>
                <th className="text-right px-4 py-2">Profit %</th>
                <th className="text-right px-4 py-2">Profit $</th>
                <th className="text-right px-4 py-2">Confidence</th>
                <th className="text-left px-4 py-2">Found</th>
                <th className="text-center px-4 py-2">Actions</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr><td colSpan={8} className="text-center py-8 text-gray-600">Loading...</td></tr>
              ) : opps.length === 0 ? (
                <tr><td colSpan={8} className="text-center py-8 text-gray-600">No opportunities in this category</td></tr>
              ) : (
                opps.map((opp) => (
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
                    <td className="px-4 py-3 text-right text-xs font-mono text-gray-400">
                      {Object.entries(opp.outcome_prices || {}).map(([k, v]: [string, any]) =>
                        `${k}: ${v.toFixed(3)}`
                      ).join(", ")}
                    </td>
                    <td className="px-4 py-3 text-right text-green-400">
                      +{opp.estimated_profit_pct?.toFixed(2)}%
                    </td>
                    <td className="px-4 py-3 text-right text-green-400">
                      ${opp.estimated_profit_usdc?.toFixed(2) || "—"}
                    </td>
                    <td className="px-4 py-3 text-right text-gray-400">
                      {opp.confidence_score ? `${(opp.confidence_score * 100).toFixed(0)}%` : "—"}
                    </td>
                    <td className="px-4 py-3 text-gray-500 text-xs">
                      {new Date(opp.found_at).toLocaleString()}
                    </td>
                    <td className="px-4 py-3 text-center">
                      {tab === "pending" && (
                        <div className="flex gap-1 justify-center">
                          <button
                            onClick={() => handleAction(opp.id, "approve")}
                            className="px-2 py-1 bg-green-600 hover:bg-green-700 text-white text-xs rounded"
                          >
                            Approve
                          </button>
                          <button
                            onClick={() => handleAction(opp.id, "reject")}
                            className="px-2 py-1 bg-red-600 hover:bg-red-700 text-white text-xs rounded"
                          >
                            Reject
                          </button>
                        </div>
                      )}
                      {tab !== "pending" && (
                        <span className="text-xs text-gray-600">{opp.status}</span>
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
