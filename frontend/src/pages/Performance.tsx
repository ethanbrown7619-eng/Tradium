import React, { useEffect, useState } from "react";
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, BarChart, Bar,
} from "recharts";
import { getTradeSummary, getTrades } from "../api";

function StatCard({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
      <p className="text-xs text-gray-500 uppercase">{label}</p>
      <p className={`text-xl font-bold mt-1 ${color || "text-gray-100"}`}>{value}</p>
    </div>
  );
}

export default function Performance() {
  const [summary, setSummary] = useState<any>(null);
  const [pnlData, setPnlData] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function load() {
      try {
        const [summaryRes, tradesRes] = await Promise.all([
          getTradeSummary("all"),
          getTrades({ limit: 200 }),
        ]);
        setSummary(summaryRes.data);

        // Build cumulative P&L chart data
        const trades = tradesRes.data.reverse();
        let cumPnl = 0;
        const chartData = trades.map((t: any) => {
          cumPnl += t.profit_loss || 0;
          return {
            date: new Date(t.executed_at).toLocaleDateString(),
            pnl: cumPnl,
            trade_pnl: t.profit_loss || 0,
          };
        });
        setPnlData(chartData);
      } catch { /* ignore */ }
      setLoading(false);
    }
    load();
  }, []);

  if (loading) return <div className="text-gray-500">Loading performance data...</div>;

  const avgProfit = summary?.total_trades > 0
    ? summary.total_pnl / summary.total_trades
    : 0;

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-bold">Performance</h2>

      {/* Summary Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard
          label="Total P&L"
          value={`$${(summary?.total_pnl || 0).toFixed(2)}`}
          color={(summary?.total_pnl || 0) >= 0 ? "text-green-400" : "text-red-400"}
        />
        <StatCard label="Total Trades" value={String(summary?.total_trades || 0)} />
        <StatCard label="Win Rate" value={`${((summary?.win_rate || 0) * 100).toFixed(1)}%`} />
        <StatCard label="Avg Profit/Trade" value={`$${avgProfit.toFixed(4)}`} />
        <StatCard label="Total Fees" value={`$${(summary?.total_fees || 0).toFixed(2)}`} />
        <StatCard label="Total Volume" value={`$${(summary?.total_volume || 0).toFixed(2)}`} />
        <StatCard
          label="Winning Trades"
          value={String(summary?.winning_trades || 0)}
          color="text-green-400"
        />
        <StatCard
          label="Capital Efficiency"
          value={summary?.total_volume > 0
            ? `${((summary.total_pnl / summary.total_volume) * 100).toFixed(2)}%`
            : "0%"
          }
        />
      </div>

      {/* P&L Chart */}
      <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
        <h3 className="text-sm font-medium text-gray-400 mb-4">Cumulative P&L</h3>
        {pnlData.length === 0 ? (
          <p className="text-gray-600 text-sm py-8 text-center">No trade data to chart</p>
        ) : (
          <ResponsiveContainer width="100%" height={300}>
            <LineChart data={pnlData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
              <XAxis dataKey="date" stroke="#6b7280" fontSize={11} />
              <YAxis stroke="#6b7280" fontSize={11} />
              <Tooltip
                contentStyle={{ background: "#111827", border: "1px solid #374151", borderRadius: 8 }}
                labelStyle={{ color: "#9ca3af" }}
              />
              <Line type="monotone" dataKey="pnl" stroke="#22c55e" strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>

      {/* Per-Trade P&L */}
      <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
        <h3 className="text-sm font-medium text-gray-400 mb-4">Per-Trade P&L</h3>
        {pnlData.length === 0 ? (
          <p className="text-gray-600 text-sm py-8 text-center">No trade data</p>
        ) : (
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={pnlData.slice(-50)}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
              <XAxis dataKey="date" stroke="#6b7280" fontSize={10} />
              <YAxis stroke="#6b7280" fontSize={11} />
              <Tooltip
                contentStyle={{ background: "#111827", border: "1px solid #374151", borderRadius: 8 }}
              />
              <Bar dataKey="trade_pnl" fill="#4f6ef7" radius={[2, 2, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}
