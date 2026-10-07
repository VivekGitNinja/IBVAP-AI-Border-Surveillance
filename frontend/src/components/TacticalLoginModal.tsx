import React, { useState } from 'react';
import { api } from '../api';

interface TacticalLoginModalProps {
  onSuccess: (user: { username: string; role: string }) => void;
  onClose?: () => void;
}

export const TacticalLoginModal: React.FC<TacticalLoginModalProps> = ({ onSuccess, onClose }) => {
  const [username, setUsername] = useState('operator');
  const [password, setPassword] = useState('operator123');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleLogin = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const res = await api.login(username, password);
      if (res && res.access_token) {
        localStorage.setItem('ibvap_token', res.access_token);
        localStorage.setItem('token', res.access_token);
        const me = await api.me().catch(() => ({ username, role: 'OPERATOR' }));
        onSuccess(me);
      } else {
        setError('Authentication failed: Missing access token response');
      }
    } catch (err: any) {
      setError(err?.message || 'Access Denied: Invalid tactical credentials');
    } finally {
      setLoading(false);
    }
  };

  const setPreset = (u: string, p: string) => {
    setUsername(u);
    setPassword(p);
    setError(null);
  };

  return (
    <div
      className="modal-backdrop"
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(2, 6, 14, 0.94)',
        backdropFilter: 'blur(16px)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 10000,
        padding: 20,
      }}
    >
      <div
        className="modal-content"
        style={{
          background: 'radial-gradient(ellipse at top, #0f1c32 0%, #060a12 100%)',
          border: '1px solid rgba(0, 240, 255, 0.4)',
          boxShadow: '0 0 40px rgba(0, 240, 255, 0.15)',
          borderRadius: 8,
          width: '100%',
          maxWidth: 460,
          padding: 30,
          fontFamily: 'monospace',
          color: '#e2effc',
        }}
      >
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 20, borderBottom: '1px solid rgba(0,240,255,0.2)', paddingBottom: 15 }}>
          <div style={{ width: 12, height: 12, borderRadius: '50%', background: '#00f0ff', boxShadow: '0 0 10px #00f0ff' }} />
          <div>
            <div style={{ fontSize: 16, fontWeight: 'bold', letterSpacing: 2, color: '#00f0ff' }}>
              IBVAP // C4ISR CLEARANCE
            </div>
            <div style={{ fontSize: 10, color: 'var(--text-secondary)', letterSpacing: 1 }}>
              TACTICAL BORDER SURVEILLANCE GATEWAY
            </div>
          </div>
        </div>

        {error && (
          <div
            style={{
              padding: '10px 14px',
              background: 'rgba(255, 42, 85, 0.15)',
              border: '1px solid #ff2a55',
              borderRadius: 4,
              color: '#ff6b8b',
              fontSize: 12,
              marginBottom: 18,
            }}
          >
            ⚠️ {error}
          </div>
        )}

        <form onSubmit={handleLogin} style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div>
            <label style={{ display: 'block', fontSize: 11, letterSpacing: 1, marginBottom: 6, color: '#94a3b8' }}>
              OPERATOR IDENTIFIER:
            </label>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              style={{
                width: '100%',
                background: '#0a101d',
                border: '1px solid rgba(0,240,255,0.3)',
                padding: '10px 12px',
                color: '#00f0ff',
                fontFamily: 'monospace',
                borderRadius: 4,
                outline: 'none',
              }}
            />
          </div>

          <div>
            <label style={{ display: 'block', fontSize: 11, letterSpacing: 1, marginBottom: 6, color: '#94a3b8' }}>
              TACTICAL SECURITY KEY:
            </label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              style={{
                width: '100%',
                background: '#0a101d',
                border: '1px solid rgba(0,240,255,0.3)',
                padding: '10px 12px',
                color: '#00f0ff',
                fontFamily: 'monospace',
                borderRadius: 4,
                outline: 'none',
              }}
            />
          </div>

          {/* Quick Presets for Demo / Evaluation */}
          <div style={{ marginTop: 6, marginBottom: 6 }}>
            <div style={{ fontSize: 10, color: '#64748b', marginBottom: 8, letterSpacing: 1 }}>
              PRE-CONFIGURED CLEARANCE TIERS:
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8 }}>
              <button
                type="button"
                onClick={() => setPreset('operator', 'operator123')}
                style={{
                  background: username === 'operator' ? 'rgba(0, 240, 255, 0.2)' : '#0f172a',
                  border: '1px solid rgba(0, 240, 255, 0.4)',
                  color: '#00f0ff',
                  padding: '6px 8px',
                  borderRadius: 4,
                  fontSize: 10,
                  cursor: 'pointer',
                  fontWeight: 'bold',
                }}
              >
                OPERATOR
              </button>
              <button
                type="button"
                onClick={() => setPreset('commander', 'commander123')}
                style={{
                  background: username === 'commander' ? 'rgba(255, 170, 0, 0.2)' : '#0f172a',
                  border: '1px solid rgba(255, 170, 0, 0.4)',
                  color: '#ffaa00',
                  padding: '6px 8px',
                  borderRadius: 4,
                  fontSize: 10,
                  cursor: 'pointer',
                  fontWeight: 'bold',
                }}
              >
                COMMANDER
              </button>
              <button
                type="button"
                onClick={() => setPreset('admin', 'admin123')}
                style={{
                  background: username === 'admin' ? 'rgba(168, 85, 247, 0.2)' : '#0f172a',
                  border: '1px solid rgba(168, 85, 247, 0.4)',
                  color: '#c084fc',
                  padding: '6px 8px',
                  borderRadius: 4,
                  fontSize: 10,
                  cursor: 'pointer',
                  fontWeight: 'bold',
                }}
              >
                ADMIN
              </button>
            </div>
          </div>

          <button
            type="submit"
            disabled={loading}
            style={{
              marginTop: 12,
              padding: '12px 20px',
              background: loading ? '#0f2b3c' : '#00f0ff',
              color: '#02060c',
              border: 'none',
              borderRadius: 4,
              fontSize: 13,
              fontWeight: 'bold',
              letterSpacing: 2,
              cursor: loading ? 'not-allowed' : 'pointer',
              boxShadow: '0 0 20px rgba(0, 240, 255, 0.3)',
            }}
          >
            {loading ? 'AUTHENTICATING TOKEN...' : 'AUTHENTICATE & ENTER CONSOLE ➔'}
          </button>
        </form>
      </div>
    </div>
  );
};
