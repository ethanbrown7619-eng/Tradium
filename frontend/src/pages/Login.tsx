import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { login, register } from "../api";
import { useStore } from "../store";

export default function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [isRegister, setIsRegister] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const setUser = useStore((s) => s.setUser);
  const navigate = useNavigate();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const fn = isRegister ? register : login;
      const res = await fn(email, password);
      setUser(res.data.user, res.data.access_token);
      navigate("/");
    } catch (err: any) {
      setError(err.response?.data?.detail || "Authentication failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-950">
      <div className="w-full max-w-md p-8 bg-gray-900 rounded-2xl border border-gray-800">
        <h1 className="text-2xl font-bold text-center mb-2 text-brand-500">Tradium</h1>
        <p className="text-center text-gray-500 text-sm mb-8">Polymarket Arbitrage Bot</p>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm text-gray-400 mb-1">Email</label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full px-4 py-2 bg-gray-800 border border-gray-700 rounded-lg text-gray-100 focus:outline-none focus:border-brand-500"
              required
            />
          </div>
          <div>
            <label className="block text-sm text-gray-400 mb-1">Password</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full px-4 py-2 bg-gray-800 border border-gray-700 rounded-lg text-gray-100 focus:outline-none focus:border-brand-500"
              required
              minLength={6}
            />
          </div>

          {error && <p className="text-red-400 text-sm">{error}</p>}

          <button
            type="submit"
            disabled={loading}
            className="w-full py-2 bg-brand-600 hover:bg-brand-700 text-white rounded-lg font-medium transition-colors disabled:opacity-50"
          >
            {loading ? "..." : isRegister ? "Create Account" : "Sign In"}
          </button>
        </form>

        <p className="text-center text-gray-500 text-sm mt-4">
          {isRegister ? "Already have an account?" : "Need an account?"}{" "}
          <button
            onClick={() => setIsRegister(!isRegister)}
            className="text-brand-500 hover:underline"
          >
            {isRegister ? "Sign in" : "Register"}
          </button>
        </p>
      </div>
    </div>
  );
}
