import React, { useEffect, useState } from "react";
import { getConfig, updateConfig, setupWallet, disconnectWallet, getWalletBalance } from "../api";
import { useStore } from "../store";

export default function Configuration() {
  const { config, setConfig, walletAddress, walletBalance, setWallet } = useStore();
  const [form, setForm] = useState<any>(null);
  const [privateKey, setPrivateKey] = useState("");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    async function load() {
      try {
        const res = await getConfig();
        setConfig(res.data.settings);
        setForm(res.data.settings);
        setWallet(res.data.wallet_address, null);
        if (res.data.wallet_address) {
          const balRes = await getWalletBalance().catch(() => ({ data: { usdc_balance: null } }));
          setWallet(res.data.wallet_address, balRes.data.usdc_balance);
        }
      } catch { /* ignore */ }
    }
    load();
  }, [setConfig, setWallet]);

  const handleSave = async () => {
    setSaving(true);
    setMessage("");
    try {
      const res = await updateConfig(form);
      setConfig(res.data.settings);
      setMessage("Settings saved");
    } catch (err: any) {
      setMessage(err.response?.data?.detail || "Failed to save");
    }
    setSaving(false);
  };

  const handleWalletSetup = async () => {
    if (!privateKey.trim()) return;
    try {
      const res = await setupWallet(privateKey);
      setWallet(res.data.wallet_address, null);
      setPrivateKey("");
      setMessage("Wallet connected");
    } catch (err: any) {
      setMessage(err.response?.data?.detail || "Invalid private key");
    }
  };

  const handleDisconnect = async () => {
    if (window.confirm("Disconnect wallet? The encrypted key will be deleted.")) {
      await disconnectWallet();
      setWallet(null, null);
      setMessage("Wallet disconnected");
    }
  };

  if (!form) return <div className="text-gray-500">Loading configuration...</div>;

  const Field = ({ label, field, type = "number", ...props }: any) => (
    <div>
      <label className="block text-xs text-gray-500 mb-1">{label}</label>
      <input
        type={type}
        value={form[field] ?? ""}
        onChange={(e) => setForm({ ...form, [field]: type === "number" ? parseFloat(e.target.value) || 0 : e.target.value })}
        className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-200 focus:outline-none focus:border-brand-500"
        {...props}
      />
    </div>
  );

  const Toggle = ({ label, field }: { label: string; field: string }) => (
    <label className="flex items-center justify-between py-2">
      <span className="text-sm text-gray-300">{label}</span>
      <button
        type="button"
        onClick={() => setForm({ ...form, [field]: !form[field] })}
        className={`relative w-10 h-5 rounded-full transition-colors ${
          form[field] ? "bg-brand-600" : "bg-gray-700"
        }`}
      >
        <span className={`absolute top-0.5 w-4 h-4 bg-white rounded-full transition-transform ${
          form[field] ? "translate-x-5" : "translate-x-0.5"
        }`} />
      </button>
    </label>
  );

  return (
    <div className="space-y-6 max-w-3xl">
      <h2 className="text-2xl font-bold">Configuration</h2>

      {message && (
        <div className={`px-4 py-2 rounded-lg text-sm ${
          message.includes("Failed") || message.includes("Invalid")
            ? "bg-red-900/30 text-red-400"
            : "bg-green-900/30 text-green-400"
        }`}>
          {message}
        </div>
      )}

      {/* Wallet */}
      <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 space-y-3">
        <h3 className="text-sm font-medium text-gray-400">Wallet</h3>
        {walletAddress ? (
          <div className="space-y-2">
            <p className="text-sm text-gray-300 font-mono">{walletAddress}</p>
            {walletBalance != null && (
              <p className="text-sm text-gray-400">Balance: <span className="text-green-400">${walletBalance.toFixed(2)} USDC</span></p>
            )}
            <button onClick={handleDisconnect} className="text-sm text-red-400 hover:text-red-300">
              Disconnect wallet
            </button>
          </div>
        ) : (
          <div className="flex gap-2">
            <input
              type="password"
              placeholder="Enter Polygon wallet private key"
              value={privateKey}
              onChange={(e) => setPrivateKey(e.target.value)}
              className="flex-1 px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-200"
            />
            <button
              onClick={handleWalletSetup}
              className="px-4 py-2 bg-brand-600 hover:bg-brand-700 text-white text-sm rounded-lg"
            >
              Connect
            </button>
          </div>
        )}
      </div>

      {/* Master Controls */}
      <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 space-y-1">
        <h3 className="text-sm font-medium text-gray-400 mb-2">Master Controls</h3>
        <Toggle label="Bot Active" field="bot_active" />
        <Toggle label="Paper Trading Mode" field="paper_trading" />
        {form.paper_trading && (
          <p className="text-xs text-yellow-500 px-1">Paper mode: no real transactions will be submitted</p>
        )}
      </div>

      {/* Trading Settings */}
      <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 space-y-4">
        <h3 className="text-sm font-medium text-gray-400">Trading Settings</h3>
        <div className="grid grid-cols-2 gap-4">
          <Field label="Min Profit %" field="min_profit_pct" step="0.1" />
          <Field label="Min Profit USDC" field="min_profit_usdc" step="0.1" />
          <Field label="Max Trade Size (USDC)" field="max_trade_size" />
          <Field label="Max Capital Deployed (USDC)" field="max_capital_deployed" />
          <Field label="Min Liquidity (USDC)" field="min_liquidity" />
          <Field label="Scan Interval (seconds)" field="scan_interval" min={5} max={300} />
          <Field label="USDC Budget" field="usdc_budget" />
          <Field label="Reserve Amount" field="reserve_amount" />
        </div>
        <Toggle label="Auto-Execute Binary Arbitrage" field="auto_execute_binary" />
        <Toggle label="Auto-Execute Multi-Outcome Arbitrage" field="auto_execute_multi" />
      </div>

      {/* Risk Controls */}
      <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 space-y-4">
        <h3 className="text-sm font-medium text-gray-400">Risk Controls</h3>
        <div className="grid grid-cols-2 gap-4">
          <Field label="Max Daily Loss (USDC)" field="max_daily_loss" />
          <Field label="Max Trades Per Hour" field="max_trades_per_hour" />
          <Field label="Cooldown After Fail (seconds)" field="cooldown_after_fail" />
        </div>
      </div>

      {/* Notifications */}
      <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 space-y-3">
        <h3 className="text-sm font-medium text-gray-400">Notifications</h3>
        <Field label="Email Address" field="notify_email" type="email" />
        <Toggle label="Notify on Trade Executed" field="notify_on_trade" />
        <Toggle label="Notify on Opportunity Found" field="notify_on_opportunity" />
        <Toggle label="Notify on Daily Summary" field="notify_on_daily_summary" />
        <Toggle label="Notify on Error" field="notify_on_error" />
        <Toggle label="Notify on Kill Switch" field="notify_on_kill_switch" />
      </div>

      {/* Categories */}
      <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 space-y-3">
        <h3 className="text-sm font-medium text-gray-400">Categories to Monitor</h3>
        <div className="flex flex-wrap gap-2">
          {["all", "politics", "crypto", "sports", "economics"].map((cat) => (
            <button
              key={cat}
              onClick={() => {
                const cats = form.categories || [];
                if (cat === "all") {
                  setForm({ ...form, categories: ["all"] });
                } else {
                  const filtered = cats.filter((c: string) => c !== "all");
                  const updated = filtered.includes(cat)
                    ? filtered.filter((c: string) => c !== cat)
                    : [...filtered, cat];
                  setForm({ ...form, categories: updated.length === 0 ? ["all"] : updated });
                }
              }}
              className={`px-3 py-1 rounded-lg text-xs transition-colors ${
                (form.categories || []).includes(cat)
                  ? "bg-brand-600 text-white"
                  : "bg-gray-800 text-gray-400 hover:bg-gray-700"
              }`}
            >
              {cat}
            </button>
          ))}
        </div>
      </div>

      {/* Save */}
      <button
        onClick={handleSave}
        disabled={saving}
        className="w-full py-3 bg-brand-600 hover:bg-brand-700 text-white font-medium rounded-xl transition-colors disabled:opacity-50"
      >
        {saving ? "Saving..." : "Save Configuration"}
      </button>
    </div>
  );
}
