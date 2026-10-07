import { useEffect, useRef, useState, useCallback } from 'react';
import { useTacticalStore } from '../store/useTacticalStore';

export function useLiveEvents() {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<any>(null);
  const seenEventsRef = useRef<Set<string>>(new Set());
  const setWsStatus = useTacticalStore((s) => s.setWsStatus);
  const addToast = useTacticalStore((s) => s.addToast);
  const fetchInitialData = useTacticalStore((s) => s.fetchInitialData);
  const showLoginModal = useTacticalStore((s) => s.showLoginModal);

  const connect = useCallback(() => {
    const token = typeof localStorage !== 'undefined' ? (localStorage.getItem('ibvap_token') || localStorage.getItem('token')) : null;
    if (!token) {
      setWsStatus('disconnected');
      return;
    }

    let url = 'ws://127.0.0.1:8001/ws/events';
    if (import.meta.env.VITE_API_URL) {
      url = import.meta.env.VITE_API_URL.replace(/^http/, 'ws').replace(/\/$/, '') + '/ws/events';
    } else if (typeof window !== 'undefined') {
      const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      if (window.location.port === '5173') {
        url = `${proto}//${window.location.hostname}:8001/ws/events`;
      } else {
        url = `${proto}//${window.location.host}/ws/events`;
      }
    }

    url += `?token=${encodeURIComponent(token)}`;

    setWsStatus('connecting');

    try {
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => setWsStatus('connected');
      ws.onclose = () => {
        setWsStatus('disconnected');
        reconnectTimeoutRef.current = setTimeout(connect, 4000);
      };
      ws.onerror = () => ws.close();

      ws.onmessage = (e) => {
        try {
          const m = JSON.parse(e.data);
          if (m.type === 'incident' || m.type === 'incident_created') {
            const inc = m.data || {};
            const dedupKey = inc.idempotency_key || inc.event_id || inc.incident_code || (inc.id ? `id-${inc.id}` : null);
            if (dedupKey) {
              if (seenEventsRef.current.has(dedupKey)) {
                return;
              }
              seenEventsRef.current.add(dedupKey);
              if (seenEventsRef.current.size > 200) {
                const first = seenEventsRef.current.values().next().value;
                seenEventsRef.current.delete(first);
              }
            }
            addToast({
              title: `TACTICAL ALERT // ${inc.incident_type || 'BREACH DETECTED'}`,
              subtitle: `Sector ${inc.bop || 'BOP-01'} • Threat: ${(inc.threat_score || 85).toFixed(0)} • ${inc.summary || 'Perimeter activity detected'}`,
              severity: inc.severity || 'HIGH',
              incidentId: inc.id,
            });
            fetchInitialData();
          } else if (m.type === 'detection' || m.type === 'rule_alert' || m.type === 'alert') {
            const det = m.data || {};
            addToast({
              title: `LIVE DETECTION // ${det.class_name ? det.class_name.toUpperCase() : 'SURVEILLANCE EVENT'}`,
              subtitle: `Camera: ${det.camera_name || 'CAM-01'} • Conf: ${((det.confidence || 0.88) * 100).toFixed(0)}%`,
              severity: (det.confidence || 0) > 0.85 ? 'HIGH' : 'MEDIUM',
            });
          }
        } catch {}
      };
    } catch {
      setWsStatus('disconnected');
      reconnectTimeoutRef.current = setTimeout(connect, 5000);
    }
  }, [setWsStatus, addToast, fetchInitialData]);

  useEffect(() => {
    if (!showLoginModal) {
      connect();
    }
    return () => {
      if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current);
      if (wsRef.current) wsRef.current.close();
    };
  }, [connect, showLoginModal]);
}

export function useLiveVideoFeed(cameraId: number, onFpsUpdate?: (fps: number) => void) {
  const [wsConnected, setWsConnected] = useState(false);
  const [latencyMs, setLatencyMs] = useState(12);
  const wsRef = useRef<WebSocket | null>(null);
  const lastFrameBlobUrlRef = useRef<string | null>(null);
  const frameCountRef = useRef(0);
  const lastFpsCalcRef = useRef(Date.now());
  const [currentFrameUrl, setCurrentFrameUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!cameraId) return;

    const token = typeof localStorage !== 'undefined' ? (localStorage.getItem('ibvap_token') || localStorage.getItem('token')) : null;
    if (!token) return;

    let isSubscribed = true;
    let wsUrl = `ws://127.0.0.1:8001/ws/live/${cameraId}`;
    if (typeof window !== 'undefined') {
      const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      if (window.location.port === '5173') {
        wsUrl = `${proto}//${window.location.hostname}:8001/ws/live/${cameraId}`;
      } else {
        wsUrl = `${proto}//${window.location.host}/ws/live/${cameraId}`;
      }
    }

    wsUrl += `?token=${encodeURIComponent(token)}`;

    try {
      const ws = new WebSocket(wsUrl);
      ws.binaryType = 'arraybuffer';
      wsRef.current = ws;
      let lastFrameTime = Date.now();

      ws.onopen = () => {
        if (!isSubscribed) return;
        setWsConnected(true);
      };

      ws.onmessage = (evt) => {
        if (!isSubscribed) return;
        const now = Date.now();
        setLatencyMs(Math.max(8, Math.min(65, now - lastFrameTime)));
        lastFrameTime = now;

        frameCountRef.current += 1;
        if (now - lastFpsCalcRef.current >= 1000) {
          const fps = Math.round((frameCountRef.current * 1000) / (now - lastFpsCalcRef.current));
          frameCountRef.current = 0;
          lastFpsCalcRef.current = now;
          if (onFpsUpdate) onFpsUpdate(fps);
        }

        const blob = new Blob([evt.data], { type: 'image/jpeg' });
        const url = URL.createObjectURL(blob);
        if (lastFrameBlobUrlRef.current) {
          URL.revokeObjectURL(lastFrameBlobUrlRef.current);
        }
        lastFrameBlobUrlRef.current = url;
        setCurrentFrameUrl(url);
      };

      ws.onclose = () => {
        if (isSubscribed) setWsConnected(false);
      };
      ws.onerror = () => ws.close();
    } catch {
      setWsConnected(false);
    }

    return () => {
      isSubscribed = false;
      if (wsRef.current) wsRef.current.close();
      if (lastFrameBlobUrlRef.current) {
        URL.revokeObjectURL(lastFrameBlobUrlRef.current);
      }
    };
  }, [cameraId, onFpsUpdate]);

  return { wsConnected, latencyMs, currentFrameUrl };
}
