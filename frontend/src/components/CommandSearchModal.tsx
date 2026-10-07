import React, { useState, useEffect, useRef } from 'react';
import type { Camera, Incident } from '../types';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  cameras: Camera[];
  incidents: Incident[];
  onSelectCamera: (camId: number) => void;
  onSelectIncident: (incId: number) => void;
  onNavigatePage: (pageId: string) => void;
}

export const CommandSearchModal: React.FC<Props> = ({
  isOpen,
  onClose,
  cameras,
  incidents,
  onSelectCamera,
  onSelectIncident,
  onNavigatePage,
}) => {
  const [query, setQuery] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isOpen) {
      setTimeout(() => inputRef.current?.focus(), 50);
    } else {
      setQuery('');
    }
  }, [isOpen]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        if (isOpen) onClose();
      } else if (e.key === 'Escape' && isOpen) {
        onClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  const q = query.toLowerCase().trim();

  // Filter items
  const filteredPages = [
    { id: 'dashboard', name: 'C4ISR Command Center', category: 'Navigation', icon: '⚡' },
    { id: 'cameras', name: 'Tactical Feeds & Matrix', category: 'Navigation', icon: '📹' },
    { id: 'anpr', name: 'ANPR Checkpost & License Plates', category: 'Navigation', icon: '🚗' },
    { id: 'frs', name: 'FRS Watchlist & Biometrics', category: 'Navigation', icon: '👤' },
    { id: 'qrt', name: 'Quick Reaction Team (QRT) Patrols', category: 'Navigation', icon: '🛡️' },
    { id: 'thermal', name: 'FLIR Thermal & Drone Recon', category: 'Navigation', icon: '🛰️' },
    { id: 'incidents', name: 'Threat Queue & Incidents', category: 'Navigation', icon: '⚠️' },
    { id: 'evidence', name: 'Evidence Locker & Section 65B', category: 'Navigation', icon: '🔒' },
    { id: 'audit', name: 'Tamper-Evident Audit Ledger', category: 'Navigation', icon: '📜' },
    { id: 'health', name: 'Edge Node Diagnostics', category: 'Navigation', icon: '🩺' },
    { id: 'demo', name: 'War Gaming Scenarios & Simulation', category: 'Navigation', icon: '🎮' },
  ].filter(p => !q || (p.name || '').toLowerCase().includes(q) || (p.id || '').toLowerCase().includes(q));

  const filteredCameras = (cameras || []).filter(c => {
    if (!c) return false;
    if (!q) return true;
    const name = (c.name || '').toLowerCase();
    const bop = (c.bop || '').toLowerCase();
    const type = (c.camera_type || '').toLowerCase();
    return name.includes(q) || bop.includes(q) || type.includes(q);
  });

  const filteredIncidents = (incidents || []).filter(i => {
    if (!i) return false;
    if (!q) return true;
    const code = (i.incident_code || '').toLowerCase();
    const title = (i.title || '').toLowerCase();
    const sev = (i.severity || '').toLowerCase();
    return code.includes(q) || title.includes(q) || sev.includes(q);
  });

  return (
    <div className="cmd-k-overlay" onClick={onClose}>
      <div className="cmd-k-modal" onClick={e => e.stopPropagation()}>
        <div className="cmd-k-search-box">
          <span style={{ color: '#00f0ff', fontSize: 18 }}>🔍</span>
          <input
            ref={inputRef}
            className="cmd-k-input"
            placeholder="Type a command, camera, BOP sector, or incident code... (Esc to close)"
            value={query}
            onChange={e => setQuery(e.target.value)}
          />
          <span className="cmd-k-kbd">ESC</span>
        </div>

        <div className="cmd-k-results">
          {/* Tactical Pages */}
          {filteredPages.length > 0 && (
            <div>
              <div className="cmd-k-category-label">Quick Navigation</div>
              {filteredPages.slice(0, 5).map(p => (
                <div
                  key={p.id}
                  className="cmd-k-item"
                  onClick={() => {
                    onNavigatePage(p.id);
                    onClose();
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <span>{p.icon}</span>
                    <span style={{ fontWeight: 600 }}>{p.name}</span>
                  </div>
                  <span className="cmd-k-item-badge">PAGE</span>
                </div>
              ))}
            </div>
          )}

          {/* Cameras */}
          {filteredCameras.length > 0 && (
            <div>
              <div className="cmd-k-category-label" style={{ marginTop: 8 }}>Surveillance Feeds</div>
              {filteredCameras.slice(0, 5).map(c => (
                <div
                  key={c.id}
                  className="cmd-k-item"
                  onClick={() => {
                    onNavigatePage('cameras');
                    onSelectCamera(c.id);
                    onClose();
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <span style={{ width: 8, height: 8, borderRadius: '50%', background: c.status === 'ONLINE' ? '#00ff9d' : '#ff2a55' }} />
                    <span><b>{c.bop || 'BOP-01'}</b> — {c.name}</span>
                  </div>
                  <span className="cmd-k-item-badge" style={{ color: '#00ff9d', borderColor: '#00ff9d' }}>
                    {c.stream_url?.startsWith('demo://') ? 'SIMULATION' : 'LIVE HW'}
                  </span>
                </div>
              ))}
            </div>
          )}

          {/* Incidents */}
          {filteredIncidents.length > 0 && (
            <div>
              <div className="cmd-k-category-label" style={{ marginTop: 8 }}>Security Incidents</div>
              {filteredIncidents.slice(0, 4).map(i => (
                <div
                  key={i.id}
                  className="cmd-k-item"
                  onClick={() => {
                    onSelectIncident(i.id);
                    onClose();
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <span style={{ color: i.severity === 'CRITICAL' ? '#ff2a55' : '#ffb700' }}>⚠️</span>
                    <span><b>{i.incident_code}</b>: {i.title}</span>
                  </div>
                  <span className="cmd-k-item-badge" style={{ color: '#ff2a55', borderColor: '#ff2a55' }}>
                    SCORE {i.threat_score.toFixed(0)}
                  </span>
                </div>
              ))}
            </div>
          )}

          {filteredPages.length === 0 && filteredCameras.length === 0 && filteredIncidents.length === 0 && (
            <div style={{ padding: '32px 16px', textAlign: 'center', color: '#557585', fontSize: 13 }}>
              No commands or nodes matching "{query}"
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
