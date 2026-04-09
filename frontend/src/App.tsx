import React from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { useStore } from "./store";
import Layout from "./components/Layout";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import Scanner from "./pages/Scanner";
import Opportunities from "./pages/Opportunities";
import Trades from "./pages/Trades";
import Performance from "./pages/Performance";
import Configuration from "./pages/Configuration";
import Markets from "./pages/Markets";

function ProtectedRoute({ children }: { children: React.ReactElement }) {
  const token = useStore((s) => s.token);
  if (!token) return <Navigate to="/login" replace />;
  return children;
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route
          path="/*"
          element={
            <ProtectedRoute>
              <Layout>
                <Routes>
                  <Route path="/" element={<Dashboard />} />
                  <Route path="/scanner" element={<Scanner />} />
                  <Route path="/opportunities" element={<Opportunities />} />
                  <Route path="/trades" element={<Trades />} />
                  <Route path="/performance" element={<Performance />} />
                  <Route path="/config" element={<Configuration />} />
                  <Route path="/markets" element={<Markets />} />
                </Routes>
              </Layout>
            </ProtectedRoute>
          }
        />
      </Routes>
    </BrowserRouter>
  );
}
