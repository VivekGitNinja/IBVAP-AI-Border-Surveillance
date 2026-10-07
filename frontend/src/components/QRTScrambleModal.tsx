import React, { useState, useEffect } from 'react';
import { api } from '../api';
import { Incident } from '../types';

interface QRTScrambleModalProps {
  incident: Incident;
  onClose: () => void;
  onDispatched?: (team: any) => void;
}

export function QRTScrambleModal({ incident, onClose, onDispatched }: QRTScrambleModalProps) {
  const [teams, setTeams] = useState<any[]>([]);
  const [selectedTeamId, setSelectedTeamId] = useState<number>(1);
  const [targetSector, setTargetSector] = useState(incident.camera_name || 'BOP-01 Sector Alpha');
  const [orders, setOrders] = useState('Emergency Perimeter Interception & Armed Containment');
  const [isScrambling, setIsScrambling] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);

  useEffect(() => {
    api.qrtTeams()
      .then((data) => {
        setTeams(data || []);
        if (data && data.length > 0) setSelectedTeamId(data[0].id);
      })
      .catch(() => {});
  }, []);

  const selectedTeam = teams.find((t) => t.id === selectedTeamId) || teams[0];

  const handleScramble = async () => {
    setIsScrambling(true);
    try {
      const res = await api.dispatchQrt({
        team_id: selectedTeamId,
        incident_id: incident.id,
        target_sector: targetSector,
        orders: orders,
      });

      setFeedback(`🚨 QRT SCRAMBLE CONFIRMED: ${res.team?.name || 'Strike Unit'} dispatched to ${targetSector}! ETA: ${res.eta_minutes || 4} mins.`);
      if (onDispatched) onDispatched(res.team);
      setTimeout(() => {
        onClose();
      }, 1500);
    } catch (err: any) {
      setFeedback(`Error dispatching QRT: ${err.message || 'Network error'}`);
    }
    setIsScrambling(false);
  };

  return (
    <div
      className="modal-backdrop"
      onClick={onClose}
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(2, 6, 14, 0.92)',
        backdropFilter: 'blur(10px)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 9999,
        padding: 20,
      }}
    >
      <div
        className="modal-content"
        onClick={(e) => e.stopPropagation()}
        style={{
          background: '#0a0e1c',
          border: '1px solid rgba(255, 42, 85, 0.6)',
          borderRadius: 8,
          maxWidth: 640,
          width: '100%',
          padding: 24,
          boxShadow: '0 0 70px rgba(255, 42, 85, 0.35)',
          position: 'relative',
        }}
      >
        {/* Header */}
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'flex-start',
            borderBottom: '1px solid rgba(255, 42, 85, 0.3)',
            paddingBottom: 14,
          }}
        >
          <div>
            <div style={{ fontSize: 10, color: '#ff2a55', letterSpacing: 2, fontFamily: 'var(--font-mono)' }}>
              TACTICAL ARMED RESPONSE DIRECTIVE
            </div>
            <h2 style={{ margin: '4px 0 0', fontSize: 18, color: '#fff', letterSpacing: 0.5 }}>
              ⚡ Scramble Quick Reaction Team (QRT)
            </h2>
          </div>
          <button className="btn btn-secondary btn-sm" onClick={onClose}>
            ✕ Close
          </button>
        </div>

        {/* Target Incident Context */}
        <div
          style={{
            margin: '16px 0',
            padding: 12,
            background: 'rgba(255, 42, 85, 0.08)',
            border: '1px solid rgba(255, 42, 85, 0.25)',
            borderRadius: 6,
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ fontSize: 12, fontWeight: 700, color: '#fff' }}>
              INCIDENT: <b style={{ color: '#00f0ff' }}>{incident.incident_code}</b>
            </span>
            <span
              style={{
                fontSize: 11,
                padding: '2px 8px',
                borderRadius: 3,
                background: '#ff2a55',
                color: '#fff',
                fontWeight: 800,
              }}
            >
              THREAT SCORE: {incident.threat_score.toFixed(0)}/100
            </span>
          </div>
          <div style={{ fontSize: 11, color: '#cbd5e1', marginTop: 4 }}>
            {incident.title} • {incident.zone_name || 'Restricted Zero-Line'}
          </div>
        </div>

        {/* Form Controls */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          {/* Select Strike Unit */}
          <div>
            <label style={{ fontSize: 11, color: 'var(--text-secondary)' }}>Select Available Strike Unit:</label>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginTop: 6 }}>
              {teams.map((t) => {
                const isSel = t.id === selectedTeamId;
                return (
                  <div
                    key={t.id}
                    onClick={() => setSelectedTeamId(t.id)}
                    style={{
                      padding: 10,
                      background: isSel ? 'rgba(0, 240, 255, 0.12)' : 'rgba(255, 255, 255, 0.03)',
                      border: `1px solid ${isSel ? '#00f0ff' : 'rgba(255, 255, 255, 0.1)'}`,
                      borderRadius: 4,
                      cursor: 'pointer',
                      transition: 'all 0.15s ease',
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                      <b style={{ fontSize: 12, color: isSel ? '#00f0ff' : '#fff' }}>{t.name}</b>
                      <span style={{ fontSize: 9, color: '#00ff9d' }}>{t.status}</span>
                    </div>
                    <div style={{ fontSize: 10, color: 'var(--text-ghost)', marginTop: 4 }}>
                      {t.vehicle} • {t.strength} Commandos
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Target Sector */}
          <div>
            <label style={{ fontSize: 11, color: 'var(--text-secondary)' }}>Vector Destination (Target Sector):</label>
            <input
              type="text"
              value={targetSector}
              onChange={(e) => setTargetSector(e.target.value)}
              style={{
                width: '100%',
                background: '#050a16',
                border: '1px solid #333',
                color: '#fff',
                padding: '8px 10px',
                borderRadius: 4,
                fontSize: 12,
                marginTop: 4,
              }}
            />
          </div>

          {/* Orders */}
          <div>
            <label style={{ fontSize: 11, color: 'var(--text-secondary)' }}>Tactical Mission Directive:</label>
            <input
              type="text"
              value={orders}
              onChange={(e) => setOrders(e.target.value)}
              style={{
                width: '100%',
                background: '#050a16',
                border: '1px solid #333',
                color: '#fff',
                padding: '8px 10px',
                borderRadius: 4,
                fontSize: 12,
                marginTop: 4,
              }}
            />
          </div>

          {/* Estimated Response ETA Card */}
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              padding: '10px 14px',
              background: 'rgba(0, 240, 255, 0.05)',
              border: '1px solid rgba(0, 240, 255, 0.2)',
              borderRadius: 4,
              fontSize: 11,
            }}
          >
            <div>
              <span style={{ color: 'var(--text-ghost)' }}>COMMANDER IN CHARGE:</span>{' '}
              <b style={{ color: '#fff' }}>{selectedTeam?.commander || 'Sub-Inspector V. K. Sharma'}</b>
            </div>
            <div>
              <span style={{ color: 'var(--text-ghost)' }}>ESTIMATED TIME OF ARRIVAL:</span>{' '}
              <b style={{ color: '#00ff9d', fontFamily: 'var(--font-mono)' }}>04:00 MINS</b>
            </div>
          </div>
        </div>

        {feedback && (
          <div
            style={{
              marginTop: 14,
              padding: '10px 12px',
              borderRadius: 4,
              fontSize: 12,
              background: String(feedback).includes('🚨') ? 'rgba(0, 255, 157, 0.15)' : 'rgba(255, 42, 85, 0.15)',
              border: String(feedback).includes('🚨') ? '1px solid #00ff9d' : '1px solid #ff2a55',
              color: String(feedback).includes('🚨') ? '#00ff9d' : '#ff7a8a',
            }}
          >
            {feedback}
          </div>
        )}

        {/* Modal Actions */}
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 20 }}>
          <button className="btn btn-secondary btn-sm" onClick={onClose} disabled={isScrambling}>
            Cancel
          </button>
          <button
            className="btn btn-danger btn-sm"
            onClick={handleScramble}
            disabled={isScrambling}
            style={{
              fontWeight: 800,
              letterSpacing: 1,
              padding: '8px 20px',
              background: '#ff2a55',
              borderColor: '#ff2a55',
              color: '#fff',
            }}
          >
            {isScrambling ? 'Scrambling Units...' : '⚡ CONFIRM IMMEDIATE SCRAMBLE'}
          </button>
        </div>
      </div>
    </div>
  );
}
