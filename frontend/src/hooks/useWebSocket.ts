import { useEffect, useRef, useCallback } from "react";
import { useStore } from "../store";

const WS_URL = process.env.REACT_APP_WS_URL || "ws://localhost:8000/ws";

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null);
  const token = useStore((s) => s.token);
  const setWsConnected = useStore((s) => s.setWsConnected);
  const addScannerEvent = useStore((s) => s.addScannerEvent);

  const connect = useCallback(() => {
    if (!token) return;
    const ws = new WebSocket(`${WS_URL}?token=${token}`);

    ws.onopen = () => setWsConnected(true);
    ws.onclose = () => {
      setWsConnected(false);
      // Reconnect after 5 seconds
      setTimeout(connect, 5000);
    };
    ws.onerror = () => ws.close();
    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === "scan_result" || data.type === "trade_update" || data.type === "opportunity") {
          addScannerEvent(data);
        }
      } catch {
        // ignore parse errors
      }
    };

    wsRef.current = ws;
  }, [token, setWsConnected, addScannerEvent]);

  useEffect(() => {
    connect();
    return () => {
      wsRef.current?.close();
    };
  }, [connect]);

  return wsRef;
}
