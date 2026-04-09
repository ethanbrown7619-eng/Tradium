import React, { useEffect, useState } from "react";
import { useStore } from "../store";
import { getConfig, getTradeSummary, getWalletBalance, getOpportunities, activateKillSwitch } from "../api";

function StatCard({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
      <p className="text-xs text-gray-500 uppercase tracking-wide">{label}</p>
      <p className={`text-2xl font-bold mt-1 ${color || "text-gray-100"}`}>{value}</p>
      {sub && <p className="text-xs text-gray-500 mt-1">{sub}</p>}
    </div>
  );
}

export default function Dashboard() {
  const { config, setConfig, setWallet, setTodaySummary, setAllTimeSummary, todaySummary, allTimeSummary, walletBalance } = useStore();
  const [recentOpps, setRecentOpps] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function load() {
      try {
        const [cfgRes, todayRes, allRes, walletRes, oppsRes] = await Promise.all([
          getConfig(),
          getTradeSummary("today"),
          getTradeSummary("all"),
          getWalletBalance().catch(() => ({ data: { usdc_balance: null } })),
          getOpportunities({ limit: 10 }),
        ]);
        setConfig(cfgRes.data.settings);
        setTodaySummary(todayRes.data);
        setAllTimeSummary(allRes.data);
        setWallet(cfgRes.data.wallet_address, walletRes.data.usdc_balance);
        setRecentOpps(oppsRes.data);
      } catch {
        // handle error
      } finally {
        setLoading(false);
      }
    }
    load();
  }, [setConfig, setWallet, setTodaySummary, setAllTimeSummary]);

  if (loading) {
    return <div className="text-gray-500">Loading dashboard...</div>;
  }

  const botStatus = config?.bot_active ? (config.paper_trading ? "Paper Trading" : "Live") : "Stopped";
  const statusColor = config?.bot_active ? (config.paper_trading ? "text-yellow-400" : "text-green-400") : "text-red-400";

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-bold">Dashboard</h2>
        <div className="flex items-center gap-3">
          <span className={`text-sm font-medium ${statusColor}`}>{botStatus}</span>
          <button
            onClick={async () => {
              if (window.confirm("Activate kill switch?")) {
                await activateKillSwitch(true);
                window.location.reload();
              }
            }}
            className="px-4 py-2 bg-red-600 hover:bg-red-700 text-white text-sm font-bold rounded-lg"
          >
            KILL SWITCH
          </button>
        </div>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Bot Status" value={botStatus} color={statusColor} />
        <StatCard
          label="Today's P&L"
          value={`$${(todaySummary?.total_pnl ?? 0).toFixed(2)}`}
          color={(todaySummary?.total_pnl ?? 0) >= 0 ? "text-green-400" : "text-red-400"}
        />
        <StatCard
          label="All-Time P&L"
          value={`$${(allTimeSummary?.total_pnl ?? 0).toFixed(2)}`}
          color={(allTimeSummary?.total_pnl ?? 0) >= 0 ? "text-green-400" : "text-red-400"}
        />
        <StatCard label="Trades Today" value={String(todaySummary?.total_trades ?? 0)} />
        <StatCard
          label="USDC Balance"
          value={walletBalance != null ? `$${walletBalance.toFixed(2)}` : "N/A"}
          sub="Polygon wallet"
        />
        <StatCard
          label="Win Rate"
          value={`${((allTimeSummary?.win_rate ?? 0) * 100).toFixed(1)}%`}
        />
        <StatCard
          label="Total Fees"
          value={`$${(allTimeSummary?.total_fees ?? 0).toFixed(2)}`}
        />
        <StatCard
          label="Total Volume"
          value={`$${(allTimeSummary?.total_volume ?? 0).toFixed(2)}`}
        />
      </div>

      {/* Recent Activity */}
      <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
        <h3 className="text-sm font-medium text-gray-400 mb-3">Recent Opportunities</h3>
        {recentOpps.length === 0 ? (
          <p className="text-sm text-gray-600">No opportunities found yet. Start the scanner from Configuration.</p>
        ) : (
          <div className="space-y-2">
            {recentOpps.map((opp: any) => (
              <div key={opp.id} className="flex items-center justify-between py-2 px-3 bg-gray-800/50 rounded-lg text-sm">
                <div>
                  <span className="text-gray-300">{opp.market_question || opp.market_id}</span>
                  <span className="ml-2 text-xs text-gray-500">{opp.strategy_type}</span>
                </div>
                <div className="flex items-center gap-4">
                  <span className="text-green-400 font-medium">
                    +{opp.estimated_profit_pct?.toFixed(2)}%
                  </span>
                  <span className={`text-xs px-2 py-0.5 rounded ${
                    opp.status === "executed" ? "bg-green-900/30 text-green-400" :
                    opp.status === "pending" ? "bg-yellow-900/30 text-yellow-400" :
                    opp.status === "failed" ? "bg-red-900/30 text-red-400" :
                    "bg-gray-800 text-gray-500"
                  }`}>
                    {opp.status}
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
