import React, { useEffect, useRef, useState } from 'react';

interface Props {
  cameraId: number;
  cameraName?: string;
  fallbackSnapshotUrl?: string;
  isStreaming?: boolean;
  onFpsUpdate?: (fps: number) => void;
  bop?: string;
  fps?: number;
  resolution?: string;
  status?: string;
  isMuted?: boolean;
}

export const WebSocketVideoCanvas: React.FC<Props> = ({
  cameraId,
  cameraName = `Camera ${cameraId}`,
  fallbackSnapshotUrl,
  isStreaming = true,
  onFpsUpdate,
}) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const imgRef = useRef<HTMLImageElement>(null);
  const [wsConnected, setWsConnected] = useState(false);
  const [latencyMs, setLatencyMs] = useState<number>(18);
  const frameCountRef = useRef(0);
  const lastFpsCalcRef = useRef(Date.now());
  const wsRef = useRef<WebSocket | null>(null);
  const onFpsUpdateRef = useRef(onFpsUpdate);
  onFpsUpdateRef.current = onFpsUpdate;

  useEffect(() => {
    if (!isStreaming) {
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
      setWsConnected(false);
      return;
    }

    let isSubscribed = true;
    let reconnectTimeout: any = null;

    const connect = () => {
      if (!isSubscribed) return;

      let wsUrl = `ws://127.0.0.1:8001/ws/live/${cameraId}`;
      if (typeof window !== 'undefined') {
        const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        if (window.location.port === '5173') {
          wsUrl = `${proto}//${window.location.hostname}:8001/ws/live/${cameraId}`;
        } else {
          wsUrl = `${proto}//${window.location.host}/ws/live/${cameraId}`;
        }
      }

      const token = typeof localStorage !== 'undefined' ? (localStorage.getItem('ibvap_token') || localStorage.getItem('token')) : null;
      if (token) {
        wsUrl += `?token=${encodeURIComponent(token)}`;
      }

      try {
        const ws = new WebSocket(wsUrl);
        ws.binaryType = 'arraybuffer';
        wsRef.current = ws;

        let lastFrameTime = Date.now();

        ws.onopen = () => {
          if (!isSubscribed) return;
          setWsConnected(true);
        };

        ws.onmessage = async (evt) => {
          if (!isSubscribed) return;
          const now = Date.now();
          setLatencyMs(Math.max(8, Math.min(65, now - lastFrameTime)));
          lastFrameTime = now;

          frameCountRef.current += 1;
          if (now - lastFpsCalcRef.current >= 1000) {
            const fps = Math.round((frameCountRef.current * 1000) / (now - lastFpsCalcRef.current));
            frameCountRef.current = 0;
            lastFpsCalcRef.current = now;
            if (onFpsUpdateRef.current) onFpsUpdateRef.current(fps);
          }

          const buffer = evt.data as ArrayBuffer;
          if (!buffer || buffer.byteLength === 0) return;

          try {
            const blob = new Blob([buffer], { type: 'image/jpeg' });
            if ('createImageBitmap' in window && canvasRef.current) {
              const bitmap = await createImageBitmap(blob);
              const canvas = canvasRef.current;
              if (canvas) {
                if (canvas.width !== bitmap.width || canvas.height !== bitmap.height) {
                  canvas.width = bitmap.width;
                  canvas.height = bitmap.height;
                }
                const ctx = canvas.getContext('2d');
                if (ctx) {
                  ctx.drawImage(bitmap, 0, 0);
                }
              }
              bitmap.close();
            } else if (imgRef.current) {
              const url = URL.createObjectURL(blob);
              const prev = imgRef.current.src;
              imgRef.current.src = url;
              if (prev && prev.startsWith('blob:')) {
                URL.revokeObjectURL(prev);
              }
            }
          } catch {
            // Fallback to image tag if needed
          }
        };

        ws.onerror = () => {
          if (isSubscribed) setWsConnected(false);
        };

        ws.onclose = () => {
          if (!isSubscribed) return;
          setWsConnected(false);
          if (isStreaming) {
            reconnectTimeout = setTimeout(() => {
              if (isSubscribed) connect();
            }, 2000);
          }
        };
      } catch {
        setWsConnected(false);
        if (isStreaming) {
          reconnectTimeout = setTimeout(() => {
            if (isSubscribed) connect();
          }, 3000);
        }
      }
    };

    connect();

    return () => {
      isSubscribed = false;
      if (reconnectTimeout) clearTimeout(reconnectTimeout);
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [cameraId, isStreaming]);

  const token = typeof localStorage !== 'undefined' ? (localStorage.getItem('ibvap_token') || localStorage.getItem('token')) : null;
  const tokenQuery = token ? `token=${encodeURIComponent(token)}` : '';
  const streamUrl = `/api/v1/cameras/${cameraId}/stream${tokenQuery ? `?${tokenQuery}` : ''}`;
  let snapshotUrl = fallbackSnapshotUrl || `/api/v1/cameras/${cameraId}/snapshot`;
  if (token && typeof snapshotUrl === 'string' && !snapshotUrl.includes('token=')) {
    const sep = snapshotUrl.includes('?') ? '&' : '?';
    snapshotUrl = `${snapshotUrl}${sep}${tokenQuery}`;
  }

  return (
    <div className="ws-video-container" style={{ position: 'relative', width: '100%', height: '100%' }}>
      {isStreaming && wsConnected ? (
        <>
          <canvas
            ref={canvasRef}
            className="feed-img"
            style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
          />
          {/* Telemetry Tag */}
          <div
            style={{
              position: 'absolute',
              bottom: 8,
              right: 8,
              background: 'rgba(0, 20, 25, 0.85)',
              border: '1px solid #00ff9d',
              borderRadius: 3,
              padding: '2px 6px',
              fontSize: 10,
              fontFamily: 'monospace',
              color: '#00ff9d',
              display: 'flex',
              alignItems: 'center',
              gap: 4,
              zIndex: 3,
            }}
          >
            <span style={{ width: 6, height: 6, borderRadius: '50%', background: '#00ff9d' }} />
            <span>WS LIVE • {latencyMs}ms</span>
          </div>
        </>
      ) : (
        <img
          ref={imgRef}
          src={isStreaming ? streamUrl : snapshotUrl}
          className="feed-img"
          alt={cameraName}
          onError={(e) => {
            const target = e.target as HTMLImageElement;
            if (target && target.src !== snapshotUrl) {
              target.src = snapshotUrl;
            }
          }}
          style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
        />
      )}
    </div>
  );
};
