import React, { useState, useEffect } from "react";
import { api } from "../api";
import type { Incident } from "../types";
import { playTacticalTone, fmtTime } from "../utils/audio";
import { QRTScrambleModal } from "../components/QRTScrambleModal";

export function QRTView({ incidents }: { incidents: Incident[] }) {
  const [teams, setTeams] = useState<any[]>([]);
  const [logs, setLogs] = useState<any[]>([]);
  const [selectedIncidentId, setSelectedIncidentId] = useState<number>(incidents[0]?.id || 1);
  const [radioMsg, setRadioMsg] = useState('');
  const [radioBroadcasts, setRadioBroadcasts] = useState<any[]>([
    {
      id: 101,
      callsign: 'CHEETAH-LEADER',
      message: 'Base, Cheetah-1 on standby at Sector Alpha forward bunker. All weapons checked.',
      time: '02:40:15 IST',
      priority: 'ROUTINE',
    },
    {
      id: 102,
      callsign: 'COBRA-ACTUAL',
      message: 'Cobra-2 moving along Ridge Line Bravo. Zero visual contact.',
      time: '02:44:20 IST',
      priority: 'ROUTINE',
    },
  ]);
  const [feedback, setFeedback] = useState<string | null>(null);

  const loadData = () => {
    api.qrtTeams().then(setTeams).catch(() => {});
    api.qrtLogs().then(setLogs).catch(() => {});
  };

  useEffect(() => {
    loadData();
  }, []);

  const handleDispatch = async (teamId: number, sector: string) => {
    playTacticalTone('click');
    try {
      const res = await api.dispatchQrt({
        team_id: teamId,
        incident_id: selectedIncidentId,
        target_sector: sector,
        orders: 'Immediate Interception & Perimeter Containment',
      });
      playTacticalTone('alert');
      setFeedback(`🚨 QRT SCRAMBLE: ${res.team.name} vectored to ${sector}. ETA: ${res.eta_minutes} minutes!`);
      loadData();
    } catch {}
  };

  const handleUpdateStatus = async (teamId: number, status: string) => {
    playTacticalTone('click');
    try {
      await api.updateQrtStatus({ team_id: teamId, status });
      playTacticalTone('verify');
      loadData();
    } catch {}
  };

  const handleSendRadio = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!radioMsg.trim()) return;
    playTacticalTone('click');
    try {
      const res = await api.broadcastRadio({
        callsign: 'C4ISR-HQ',
        message: radioMsg,
        priority: 'FLASH_TACTICAL',
      });
      setRadioBroadcasts((prev) => [
        {
          id: res.broadcast_id,
          callsign: res.callsign,
          message: res.message,
          time: new Date().toLocaleTimeString('en-GB', { timeZone: 'Asia/Kolkata', hour12: false }) + ' IST',
          priority: res.priority,
        },
        ...prev,
      ]);
      setRadioMsg('');
      playTacticalTone('verify');
    } catch {}
  };

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Tactical Quick Reaction Team (QRT) Dispatch & Command</h1>
          <p style={{ margin: 0, fontSize: 12, color: 'var(--text-secondary)' }}>
            Real-time commando unit vectoring, mission engagement tracking, ETA countdown, and encrypted VHF radio SITREPs
          </p>
        </div>
      </div>

      {feedback && (
        <div className={`test-feedback ${String(feedback).includes('🚨') ? 'fail' : 'success'}`} style={{ marginBottom: 16 }}>
          {feedback}
        </div>
      )}

      {/* QRT Fleet Readiness KPI Ribbon */}
      <div className="tactical-kpi-ribbon" style={{ marginBottom: 16 }}>
        <div className="tactical-kpi-card">
          <div className="kpi-header-label">
            <span>STRIKE UNITS DEPLOYED</span>
            <span style={{ color: '#00f0ff' }}>READINESS</span>
          </div>
          <div className="kpi-metric-val">{teams.length} UNITS</div>
        </div>
        <div className="tactical-kpi-card">
          <div className="kpi-header-label">
            <span>TOTAL COMMANDO STRENGTH</span>
            <span style={{ color: '#00ff9d' }}>OPERATORS</span>
          </div>
          <div className="kpi-metric-val" style={{ color: '#00ff9d' }}>
            30 ARMED PERSONNEL
          </div>
        </div>
        <div className="tactical-kpi-card">
          <div className="kpi-header-label">
            <span>AVG PERIMETER RESPONSE ETA</span>
            <span style={{ color: '#ffaa00' }}>SPEED</span>
          </div>
          <div className="kpi-metric-val" style={{ color: '#ffaa00' }}>
            00:04 MINS
          </div>
        </div>
        <div className="tactical-kpi-card">
          <div className="kpi-header-label">
            <span>ENCRYPTED VHF TACTICAL NET</span>
            <span style={{ color: '#00ff9d' }}>SECURE</span>
          </div>
          <div className="kpi-metric-val" style={{ fontSize: 15, color: '#00ff9d' }}>
            142.850 MHz (DRDO CRYPTO)
          </div>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 16 }}>
        {/* QRT Strike Units Grid */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          {teams.map((t) => {
            const isEnRoute = t.status === 'EN_ROUTE';
            return (
              <div key={t.id} className={`qrt-unit-card ${isEnRoute ? 'active-route' : ''}`}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                  <div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <b style={{ color: '#fff', fontSize: 15 }}>{t.name}</b>
                      <span className={`qrt-readiness-badge ${isEnRoute ? 'enroute' : 'standby'}`}>
                        {t.status}
                      </span>
                    </div>
                    <div style={{ fontSize: 11, color: '#00f0ff', fontFamily: 'var(--font-mono)', marginTop: 2 }}>
                      CALLSIGN: {t.callsign} • FREQ: {t.radio_channel}
                    </div>
                  </div>
                  <div style={{ textAlign: 'right' }}>
                    <div style={{ fontSize: 12, fontWeight: 700, color: '#fff' }}>
                      Strength: {t.strength} Commandos
                    </div>
                    <div style={{ fontSize: 10, color: 'var(--text-ghost)', marginTop: 2 }}>
                      Fuel: {t.fuel_percent}% • Battery: 100%
                    </div>
                  </div>
                </div>

                <div style={{ background: '#02060c', padding: 10, borderRadius: 4, border: '1px solid rgba(255,255,255,0.06)' }}>
                  <div style={{ fontSize: 11, color: '#e2effc' }}>
                    <b>Vehicle:</b> {t.vehicle}
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 3 }}>
                    <b>Weapons Loadout:</b> {t.weapons_readiness}
                  </div>
                  <div style={{ fontSize: 10, color: '#ffaa00', fontFamily: 'var(--font-mono)', marginTop: 4 }}>
                    SITREP: {t.last_sitrep}
                  </div>
                </div>

                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                    Sector: <b style={{ color: '#fff' }}>{t.current_sector}</b>
                  </div>
                  <div style={{ display: 'flex', gap: 8 }}>
                    {isEnRoute ? (
                      <button
                        className="btn btn-sm btn-primary"
                        onClick={() => handleUpdateStatus(t.id, 'ENGAGED')}
                      >
                        ⚡ Report Engaged / Perimeter Contained
                      </button>
                    ) : (
                      <button
                        className="btn btn-sm btn-danger"
                        onClick={() => handleDispatch(t.id, t.bop)}
                        style={{ fontWeight: 800, letterSpacing: 1 }}
                      >
                        🚨 SCRAMBLE TO INCIDENT SECTOR
                      </button>
                    )}
                    <button
                      className="btn btn-sm btn-secondary"
                      onClick={() => handleUpdateStatus(t.id, 'STANDBY_IMMEDIATE')}
                    >
                      Standby
                    </button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>

        {/* Tactical VHF Radio Terminal */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div className="panel" style={{ margin: 0, padding: 16 }}>
            <h3 style={{ color: '#00f0ff', marginBottom: 12, fontSize: 14 }}>📻 Tactical VHF Radio Terminal</h3>
            <form onSubmit={handleSendRadio} style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div>
                <label style={{ fontSize: 11, color: 'var(--text-secondary)' }}>Transmit Orders / SITREP:</label>
                <textarea
                  rows={3}
                  value={radioMsg}
                  onChange={(e) => setRadioMsg(e.target.value)}
                  placeholder="e.g. ALL UNITS: Infiltration spotted at Sector Alpha. Secure grid 14."
                  style={{
                    width: '100%',
                    background: '#040b14',
                    border: '1px solid rgba(0, 240, 255, 0.3)',
                    color: '#fff',
                    padding: 8,
                    borderRadius: 4,
                    fontSize: 12,
                    marginTop: 4,
                    resize: 'none',
                  }}
                />
              </div>
              <button className="btn btn-primary" type="submit">
                📡 Transmit Encrypted Radio Order
              </button>
            </form>
          </div>

          <div className="panel" style={{ margin: 0, padding: 16, flex: 1 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
              <h3 style={{ color: '#fff', margin: 0, fontSize: 13 }}>Live Radio Net Ledger</h3>
              <span className="panel-tag" style={{ color: '#00ff9d', borderColor: '#00ff9d' }}>CH-142.850</span>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, maxHeight: 340, overflowY: 'auto' }}>
              {radioBroadcasts.map((b) => (
                <div
                  key={b.id}
                  style={{
                    padding: 8,
                    background: 'rgba(0, 240, 255, 0.04)',
                    borderLeft: '2px solid #00f0ff',
                    borderRadius: 2,
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10 }}>
                    <b style={{ color: '#00f0ff' }}>{b.callsign}</b>
                    <span style={{ color: 'var(--text-ghost)' }}>{b.time}</span>
                  </div>
                  <div style={{ fontSize: 11, color: '#e2effc', marginTop: 3 }}>{b.message}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ─── FLIR Thermal & Aerial Drone Reconnaissance ──────────────── */
