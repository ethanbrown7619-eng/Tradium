import axios from "axios";

const API_BASE = process.env.REACT_APP_API_URL || "http://localhost:8000/api";

const api = axios.create({
  baseURL: API_BASE,
  headers: { "Content-Type": "application/json" },
});

api.interceptors.request.use((config) => {
  const token = localStorage.getItem("token");
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

api.interceptors.response.use(
  (res) => res,
  (err) => {
    if (err.response?.status === 401) {
      localStorage.removeItem("token");
      window.location.href = "/login";
    }
    return Promise.reject(err);
  }
);

// Auth
export const register = (email: string, password: string) =>
  api.post("/auth/register", { email, password });
export const login = (email: string, password: string) =>
  api.post("/auth/login", { email, password });

// Config
export const getConfig = () => api.get("/config");
export const updateConfig = (settings: any) => api.put("/config", settings);
export const setupWallet = (privateKey: string) =>
  api.post("/config/wallet", { private_key: privateKey });
export const activateKillSwitch = (activate: boolean) =>
  api.post("/config/kill-switch", { activate });

// Trades
export const getTrades = (params?: any) => api.get("/trades", { params });
export const getTradeSummary = (period?: string) =>
  api.get("/trades/summary", { params: { period } });
export const exportTradesCsv = (params?: any) =>
  api.get("/trades/export", { params, responseType: "blob" });

// Markets
export const getMarkets = (params?: any) => api.get("/markets", { params });
export const getMarketDetail = (id: string) => api.get(`/markets/${id}`);

// Opportunities
export const getOpportunities = (params?: any) =>
  api.get("/markets/opportunities", { params });
export const opportunityAction = (id: string, action: string) =>
  api.post(`/markets/opportunities/${id}/action`, { action });

// Wallet
export const getWalletBalance = () => api.get("/wallet/balance");
export const disconnectWallet = () => api.delete("/wallet");

export default api;
