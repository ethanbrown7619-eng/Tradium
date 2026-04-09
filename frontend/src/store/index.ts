import { create } from "zustand";

interface User {
  id: string;
  email: string;
}

interface BotConfig {
  bot_active: boolean;
  paper_trading: boolean;
  min_profit_pct: number;
  min_profit_usdc: number;
  max_trade_size: number;
  max_capital_deployed: number;
  min_liquidity: number;
  auto_execute_binary: boolean;
  auto_execute_multi: boolean;
  scan_interval: number;
  excluded_markets: string[];
  excluded_keywords: string[];
  categories: string[];
  usdc_budget: number;
  reserve_amount: number;
  max_daily_loss: number;
  max_trades_per_hour: number;
  cooldown_after_fail: number;
  notify_email: string | null;
  notify_on_trade: boolean;
  notify_on_opportunity: boolean;
  notify_on_daily_summary: boolean;
  notify_on_error: boolean;
  notify_on_kill_switch: boolean;
}

interface Opportunity {
  id: string;
  market_id: string;
  market_question: string;
  strategy_type: string;
  outcome_prices: Record<string, number>;
  price_sum: number;
  estimated_profit_pct: number;
  estimated_profit_usdc: number | null;
  liquidity_depth: number | null;
  confidence_score: number | null;
  status: string;
  found_at: string;
}

interface Trade {
  id: string;
  market_id: string;
  strategy_type: string;
  side: string;
  size_usdc: number;
  fill_price: number | null;
  fees_paid: number | null;
  profit_loss: number | null;
  status: string;
  is_paper: boolean;
  executed_at: string;
  tx_hash: string | null;
}

interface TradeSummary {
  total_trades: number;
  total_pnl: number;
  total_fees: number;
  total_volume: number;
  winning_trades: number;
  win_rate: number;
}

interface AppState {
  user: User | null;
  token: string | null;
  config: BotConfig | null;
  walletAddress: string | null;
  walletBalance: number | null;
  opportunities: Opportunity[];
  trades: Trade[];
  todaySummary: TradeSummary | null;
  allTimeSummary: TradeSummary | null;
  scannerFeed: any[];
  wsConnected: boolean;

  setUser: (user: User | null, token: string | null) => void;
  setConfig: (config: BotConfig) => void;
  setWallet: (address: string | null, balance: number | null) => void;
  setOpportunities: (opps: Opportunity[]) => void;
  setTrades: (trades: Trade[]) => void;
  setTodaySummary: (s: TradeSummary) => void;
  setAllTimeSummary: (s: TradeSummary) => void;
  addScannerEvent: (event: any) => void;
  setWsConnected: (connected: boolean) => void;
  logout: () => void;
}

export const useStore = create<AppState>((set) => ({
  user: null,
  token: localStorage.getItem("token"),
  config: null,
  walletAddress: null,
  walletBalance: null,
  opportunities: [],
  trades: [],
  todaySummary: null,
  allTimeSummary: null,
  scannerFeed: [],
  wsConnected: false,

  setUser: (user, token) => {
    if (token) localStorage.setItem("token", token);
    else localStorage.removeItem("token");
    set({ user, token });
  },
  setConfig: (config) => set({ config }),
  setWallet: (walletAddress, walletBalance) =>
    set({ walletAddress, walletBalance }),
  setOpportunities: (opportunities) => set({ opportunities }),
  setTrades: (trades) => set({ trades }),
  setTodaySummary: (todaySummary) => set({ todaySummary }),
  setAllTimeSummary: (allTimeSummary) => set({ allTimeSummary }),
  addScannerEvent: (event) =>
    set((s) => ({ scannerFeed: [event, ...s.scannerFeed].slice(0, 100) })),
  setWsConnected: (wsConnected) => set({ wsConnected }),
  logout: () => {
    localStorage.removeItem("token");
    set({
      user: null,
      token: null,
      config: null,
      walletAddress: null,
      walletBalance: null,
    });
  },
}));

export type { User, BotConfig, Opportunity, Trade, TradeSummary };
