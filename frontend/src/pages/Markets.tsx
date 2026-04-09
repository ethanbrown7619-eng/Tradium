import React, { useEffect, useState } from "react";
import { getMarkets, getMarketDetail } from "../api";

export default function Markets() {
  const [markets, setMarkets] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [selectedMarket, setSelectedMarket] = useState<any>(null);
  const [detail, setDetail] = useState<any>(null);

  useEffect(() => {
    async function load() {
      setLoading(true);
      try {
        const res = await getMarkets({ search: search || undefined, limit: 50 });
        setMarkets(res.data);
      } catch { /* ignore */ }
      setLoading(false);
    }
    load();
  }, [search]);

  const handleSelect = async (marketId: string) => {
    setSelectedMarket(marketId);
    try {
      const res = await getMarketDetail(marketId);
      setDetail(res.data);
    } catch { /* ignore */ }
  };

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-bold">Market Browser</h2>

      {/* Search */}
      <input
        type="text"
        placeholder="Search markets..."
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        className="w-full px-4 py-2 bg-gray-800 border border-gray-700 rounded-lg text-sm text-gray-200 focus:outline-none focus:border-brand-500"
      />

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Market List */}
        <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
          <div className="px-4 py-3 border-b border-gray-800">
            <h3 className="text-sm font-medium text-gray-400">Active Markets ({markets.length})</h3>
          </div>
          <div className="max-h-[600px] overflow-y-auto">
            {loading ? (
              <p className="text-gray-600 text-sm text-center py-8">Loading markets...</p>
            ) : markets.length === 0 ? (
              <p className="text-gray-600 text-sm text-center py-8">No markets found</p>
            ) : (
              markets.map((m: any) => {
                const tokens = m.tokens || [];
                const yesToken = tokens.find((t: any) => t.outcome?.toUpperCase() === "YES");
                const noToken = tokens.find((t: any) => t.outcome?.toUpperCase() === "NO");
                return (
                  <button
                    key={m.id}
                    onClick={() => handleSelect(m.id)}
                    className={`w-full text-left px-4 py-3 border-b border-gray-800/50 hover:bg-gray-800/30 transition-colors ${
                      selectedMarket === m.id ? "bg-gray-800/50" : ""
                    }`}
                  >
                    <p className="text-sm text-gray-300">{m.question}</p>
                    <div className="flex gap-3 mt-1 text-xs">
                      {yesToken && (
                        <span className="text-green-400">YES: {parseFloat(yesToken.price || 0).toFixed(2)}</span>
                      )}
                      {noToken && (
                        <span className="text-red-400">NO: {parseFloat(noToken.price || 0).toFixed(2)}</span>
                      )}
                      {tokens.length > 2 && (
                        <span className="text-gray-500">{tokens.length} outcomes</span>
                      )}
                      <span className="text-gray-600">Vol: ${(m.volume24hr || 0).toLocaleString()}</span>
                    </div>
                    {m.tags && (
                      <div className="flex gap-1 mt-1">
                        {m.tags.slice(0, 3).map((tag: string) => (
                          <span key={tag} className="text-xs bg-gray-800 text-gray-500 px-1.5 py-0.5 rounded">
                            {tag}
                          </span>
                        ))}
                      </div>
                    )}
                  </button>
                );
              })
            )}
          </div>
        </div>

        {/* Market Detail */}
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
          {!detail ? (
            <p className="text-gray-600 text-sm text-center py-16">Select a market to view details</p>
          ) : (
            <div className="space-y-4">
              <h3 className="text-lg font-medium text-gray-200">
                {detail.market?.question || "Market Details"}
              </h3>

              {/* Tokens */}
              <div className="space-y-2">
                <h4 className="text-xs text-gray-500 uppercase">Outcomes</h4>
                {(detail.market?.tokens || []).map((token: any) => (
                  <div key={token.token_id} className="flex justify-between items-center py-2 px-3 bg-gray-800/50 rounded-lg">
                    <span className="text-sm text-gray-300">{token.outcome}</span>
                    <span className="text-sm font-mono text-gray-200">
                      ${parseFloat(token.price || 0).toFixed(4)}
                    </span>
                  </div>
                ))}
              </div>

              {/* Order Book */}
              {detail.order_book && (
                <div className="space-y-2">
                  <h4 className="text-xs text-gray-500 uppercase">Order Book</h4>
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <p className="text-xs text-green-400 mb-1">Bids</p>
                      {(detail.order_book.bids || []).slice(0, 5).map((bid: any, i: number) => (
                        <div key={i} className="flex justify-between text-xs font-mono py-0.5">
                          <span className="text-green-400">{parseFloat(bid.price).toFixed(4)}</span>
                          <span className="text-gray-500">{parseFloat(bid.size).toFixed(0)}</span>
                        </div>
                      ))}
                    </div>
                    <div>
                      <p className="text-xs text-red-400 mb-1">Asks</p>
                      {(detail.order_book.asks || []).slice(0, 5).map((ask: any, i: number) => (
                        <div key={i} className="flex justify-between text-xs font-mono py-0.5">
                          <span className="text-red-400">{parseFloat(ask.price).toFixed(4)}</span>
                          <span className="text-gray-500">{parseFloat(ask.size).toFixed(0)}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              )}

              {/* Market Info */}
              <div className="text-xs text-gray-500 space-y-1">
                <p>ID: <span className="font-mono">{detail.market?.id}</span></p>
                <p>Condition: <span className="font-mono">{detail.market?.condition_id}</span></p>
                {detail.market?.slug && <p>Slug: {detail.market.slug}</p>}
                {detail.market?.end_date && <p>Ends: {new Date(detail.market.end_date).toLocaleDateString()}</p>}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
