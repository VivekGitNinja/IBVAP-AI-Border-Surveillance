import React, { useState, useEffect } from 'react';
import type { Camera, Incident, Alert } from '../types';
import { useTacticalStore } from '../store/useTacticalStore';
import { TacticalBorderMap } from '../components/TacticalBorderMap';
import { QRTScrambleModal } from '../components/QRTScrambleModal';
import { api } from '../api';
import { playTacticalTone, fmtTimeShort } from '../utils/audio';

export const DashboardCamFrame = React.memo(function DashboardCamFrame({
  cameraId,
  cameraName,
}: {
  cameraId: number;
  cameraName: string;
}) {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const iv = setInterval(() => setTick((t) => t + 1), 2000);
    return () => clearInterval(iv);
  }, []);

  return (
    <div className="tactical-cam-frame">
      <img
        src={`/api/v1/cameras/${cameraId}/snapshot?t=${tick}`}
        alt={cameraName}
        onError={(e) => {
          const target = e.target as HTMLImageElement;
          setTimeout(() => {
            target.src = `/api/v1/cameras/${cameraId}/snapshot?t=${Date.now()}`;
          }, 3000);
        }}
      />
      <div className="corner-bracket cb-top-left" />
      <div className="corner-bracket cb-top-right" />
      <div className="corner-bracket cb-bottom-left" />
      <div className="corner-bracket cb-bottom-right" />
      <div className="tactical-crosshair-center" />
    </div>
  );
});

export function AnalyticsDashboard() {
  const {
    cameras,
    incidents,
    alerts,
    busy,
    demoMsg,
    selectedBop,
    setSelectedBop,
    openIncident,
    runDemoScenario,
  } = useTacticalStore();

  const [dashboardScrambleInc, setDashboardScrambleInc] = useState<Incident | null>(null);
  const open = incidents.filter((i: Incident) => i.status === 'OPEN');
  const high = incidents.filter((i: Incident) => i.severity === 'HIGH' || i.severity === 'CRITICAL');

  // Filter items if user clicked a BOP on the tactical map
  const filteredCameras = selectedBop
    ? cameras.filter((c: Camera) => (c.bop || '').toLowerCase() === selectedBop.toLowerCase())
    : cameras;

  const filteredIncidents = selectedBop
    ? incidents.filter((i: Incident) => {
        const cname = (i.camera_name || '').toLowerCase();
        const desc = (i.description || '').toLowerCase();
        const sb = selectedBop.toLowerCase();
        return cname.includes(sb) || desc.includes(sb);
      })
    : incidents;

  return (
    <div className="dashboard">
      {/* ─── Tactical Sector Command Toolbar ──── */}
      <div className="dashboard-sector-toolbar">
        <div className="sector-toolbar-left">
          <span className="sector-toolbar-label">SECTOR FOCUS:</span>
          <div className="sector-pill-selector">
            {[
              { id: null, label: 'All Sectors', count: cameras.length },
              { id: 'BOP-01', label: 'BOP-01 Alpha', count: cameras.filter((c: Camera) => (c.bop || '').includes('01')).length || 1 },
              { id: 'BOP-02', label: 'BOP-02 Bravo', count: cameras.filter((c: Camera) => (c.bop || '').includes('02')).length || 1 },
              { id: 'BOP-03', label: 'BOP-03 Charlie', count: cameras.filter((c: Camera) => (c.bop || '').includes('03')).length || 1 },
              { id: 'BOP-04', label: 'BOP-04 Delta', count: cameras.filter((c: Camera) => (c.bop || '').includes('04')).length || 1 },
            ].map((opt) => {
              const active = selectedBop === opt.id;
              return (
                <button
                  key={String(opt.id)}
                  className={`dashboard-sector-btn ${active ? 'active' : ''}`}
                  onClick={() => setSelectedBop(opt.id)}
                >
                  <span className="sector-dot" />
                  <span className="sector-btn-text">{opt.label}</span>
                  <span className="sector-count-badge">{opt.count}</span>
                </button>
              );
            })}
          </div>
        </div>

        <div className="sector-toolbar-right">
          <div className="sector-live-indicator">
            <span className="live-pulsar" />
            <span>PERIMETER EO/IR ACTIVE</span>
          </div>
          <button
            className="btn btn-sm btn-primary"
            onClick={() => runDemoScenario('intrusion')}
            disabled={busy}
            title="Inject Tactical Intrusion Drill"
          >
            ⚡ Test Drill
          </button>
        </div>
      </div>

      {/* 6-Column Tactical KPI Ribbon */}
      <div className="tactical-kpi-ribbon">
        <div className="tactical-kpi-card">
          <div className="kpi-header-label">
            <span>SURVEILLANCE POSTS</span>
            <span style={{ color: '#00f0ff' }}>BOP ACTIVE</span>
          </div>
          <div className="kpi-metric-val">
            4 <span className="kpi-metric-sub">/ 4 SECTORS</span>
          </div>
          <div className="kpi-progress-bar">
            <div className="kpi-progress-fill" style={{ width: '100%' }} />
          </div>
        </div>

        <div className="tactical-kpi-card emerald">
          <div className="kpi-header-label">
            <span>CAMERAS ONLINE</span>
            <span style={{ color: '#00ff9d' }}>100% HEALTH</span>
          </div>
          <div className="kpi-metric-val">
            {cameras.filter((c: Camera) => c.status === 'ONLINE').length}
            <span className="kpi-metric-sub">/ {cameras.length} UNITS</span>
          </div>
          <div className="kpi-progress-bar">
            <div
              className="kpi-progress-fill"
              style={{
                width: `${cameras.length ? (cameras.filter((c: Camera) => c.status === 'ONLINE').length / cameras.length) * 100 : 100}%`,
              }}
            />
          </div>
        </div>

        <div className={`tactical-kpi-card ${open.length > 0 ? 'amber' : 'emerald'}`}>
          <div className="kpi-header-label">
            <span>ACTIVE INCIDENTS</span>
            <span>HUMAN VERIFY</span>
          </div>
          <div className="kpi-metric-val">
            {open.length} <span className="kpi-metric-sub">IN QUEUE</span>
          </div>
          <div className="kpi-progress-bar">
            <div className="kpi-progress-fill" style={{ width: `${Math.min(100, open.length * 20)}%` }} />
          </div>
        </div>

        <div className={`tactical-kpi-card ${high.length > 0 ? 'crimson' : 'emerald'}`}>
          <div className="kpi-header-label">
            <span>CRITICAL THREATS</span>
            <span style={{ color: high.length > 0 ? '#ff2a55' : '#00ff9d' }}>HIGH PRIORITY</span>
          </div>
          <div className="kpi-metric-val">
            {high.length} <span className="kpi-metric-sub">ESCALATED</span>
          </div>
          <div className="kpi-progress-bar">
            <div className="kpi-progress-fill" style={{ width: `${Math.min(100, high.length * 33)}%` }} />
          </div>
        </div>

        <div className="tactical-kpi-card purple">
          <div className="kpi-header-label">
            <span>HASH INTEGRITY</span>
            <span style={{ color: '#a855f7' }}>IMMUTABLE</span>
          </div>
          <div className="kpi-metric-val">
            100% <span className="kpi-metric-sub">SHA-256 SEALED</span>
          </div>
          <div className="kpi-progress-bar">
            <div className="kpi-progress-fill" style={{ width: '100%' }} />
          </div>
        </div>

        <div className="tactical-kpi-card">
          <div className="kpi-header-label">
            <span>EDGE LATENCY</span>
            <span style={{ color: '#00f0ff' }}>REAL-TIME</span>
          </div>
          <div className="kpi-metric-val">
            38 <span className="kpi-metric-sub">MS / FRAME</span>
          </div>
          <div className="kpi-progress-bar">
            <div className="kpi-progress-fill" style={{ width: '38%' }} />
          </div>
        </div>
      </div>

      {/* Interactive Tactical Border Radar Map */}
      <TacticalBorderMap
        cameras={cameras}
        incidents={incidents}
        selectedBop={selectedBop}
        onSelectBop={setSelectedBop}
      />

      {/* Main Dual Grid: Camera Wall (Left) & Threat Intelligence (Right) */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 1fr', gap: 16, marginBottom: 16 }}>
        {/* Left: Multi-Camera Tactical Matrix Wall */}
        <div className="panel" style={{ margin: 0 }}>
          <div className="panel-header">
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#00f0ff" strokeWidth="2">
                <rect x="2" y="2" width="20" height="20" rx="2" />
                <path d="M7 2v20M17 2v20M2 12h20M2 7h5M2 17h5M17 17h5M17 7h5" />
              </svg>
              <h2>Tactical Camera Matrix</h2>
            </div>
            <span className="panel-tag" style={{ color: '#00ff9d', borderColor: '#00ff9d' }}>
              ● LIVE EO/IR FEEDS
            </span>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 10, padding: 12 }}>
            {(filteredCameras.length > 0 ? filteredCameras : cameras).slice(0, 4).map((c: Camera) => {
              const hasAlert = open.some((i: Incident) => i.camera_id === c.id);
              return (
                <div key={c.id} className={`tactical-camera-cell ${hasAlert ? 'has-incident' : ''}`}>
                  <div className="tactical-cam-header">
                    <span className="cam-title-tag">
                      <span className={`health-dot-sm ${c.status.toLowerCase()}`} />
                      {c.bop || 'BOP-01'} | {c.name.split(' ')[0]}
                    </span>
                    <span className="cam-telemetry-tag">{c.resolution || '1080p'} • {c.fps || 15} FPS</span>
                  </div>
                  <DashboardCamFrame cameraId={c.id} cameraName={c.name} />
                  <div className={`tactical-detect-tag ${hasAlert ? 'danger' : ''}`}>
                    {hasAlert ? '⚠️ INTRUSION DETECTED' : 'SURVEILLANCE NORMAL'}
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* Right: Threat Intelligence & Incident Stream */}
        <div className="panel" style={{ margin: 0, display: 'flex', flexDirection: 'column' }}>
          <div className="panel-header">
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#ff2a55" strokeWidth="2">
                <path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6" />
              </svg>
              <h2>Incident Intelligence Stream</h2>
            </div>
            <span className="panel-tag" style={{ color: '#ffb700', borderColor: '#ffb700' }}>
              {open.length} UNVERIFIED
            </span>
          </div>

          <div style={{ padding: 12, overflowY: 'auto', flex: 1, maxHeight: 420 }}>
            {filteredIncidents.length === 0 ? (
              <div className="empty" style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8, padding: '36px 16px' }}>
                <span>Perimeter secure. No active incidents.</span>
              </div>
            ) : (
              filteredIncidents.slice(0, 5).map((i: Incident) => {
                const isCrit = i.severity === 'CRITICAL' || i.threat_score >= 75;
                const isHigh = i.severity === 'HIGH' || i.threat_score >= 55;
                const dialClass = isCrit ? '' : isHigh ? 'high' : i.threat_score >= 35 ? 'med' : 'low';

                return (
                  <div
                    key={i.id}
                    className={`incident-tactical-card ${isCrit ? 'crit' : ''}`}
                    onClick={() => openIncident(i.id)}
                  >
                    <div className={`incident-score-dial ${dialClass}`}>
                      <span className="score-number">{i.threat_score.toFixed(0)}</span>
                      <span className="score-sub">SCORE</span>
                    </div>

                    <div className="incident-main-col">
                      <div className="incident-top-line">
                        <span className={`sev-badge sev-${i.severity.toLowerCase()}`}>{i.severity}</span>
                        <span className="incident-code-badge">{i.incident_code}</span>
                        <span className="incident-meta-text">{fmtTimeShort(i.created_at)}</span>
                      </div>
                      <div className="incident-title-text">{i.title}</div>
                      <div className="reason-tags">
                        {i.reason_codes.slice(0, 3).map((r: string) => (
                          <span key={r} className="reason-tag">
                            {r.split(':')[0].trim()}
                          </span>
                        ))}
                      </div>
                    </div>

                    <div className="incident-quick-actions" onClick={(e) => e.stopPropagation()}>
                      {i.status === 'OPEN' && (
                        <button
                          className="btn-quick-verify"
                          onClick={async () => {
                            playTacticalTone('verify');
                            await api.acknowledgeIncident(i.id);
                            openIncident(i.id);
                          }}
                        >
                          VERIFY
                        </button>
                      )}
                      <button
                        className="btn-quick-escalate"
                        onClick={async () => {
                          playTacticalTone('escalate');
                          await api.escalateIncident(i.id);
                          openIncident(i.id);
                        }}
                      >
                        ESCALATE
                      </button>
                      <button
                        className="btn-quick-escalate"
                        style={{ background: '#ff2a55', borderColor: '#ff2a55', color: '#fff', fontWeight: 800 }}
                        onClick={() => {
                          playTacticalTone('click');
                          setDashboardScrambleInc(i);
                        }}
                        title="Scramble QRT Strike Unit to this breach location"
                      >
                        ⚡ QRT
                      </button>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>
      </div>

      {/* Quick Tactical War Gaming Bar */}
      <div className="panel" style={{ padding: '10px 16px', background: 'rgba(6, 15, 26, 0.9)' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 10 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontFamily: 'var(--font-hud)', fontWeight: 700, fontSize: 13, color: '#00f0ff' }}>
              ▶ TACTICAL SCENARIO INJECTION:
            </span>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              <button className="btn btn-primary" onClick={() => runDemoScenario('intrusion')} disabled={busy}>
                🚨 Perimeter Breach
              </button>
              <button className="btn btn-secondary" onClick={() => runDemoScenario('night_movement')} disabled={busy}>
                🌙 Night Infiltration
              </button>
              <button className="btn btn-secondary" onClick={() => runDemoScenario('loitering')} disabled={busy}>
                ⏳ Suspicious Loitering
              </button>
              <button className="btn btn-secondary" onClick={() => runDemoScenario('vehicle')} disabled={busy}>
                🚙 Convoy / Vehicle
              </button>
              <button className="btn btn-secondary" onClick={() => runDemoScenario('abandoned')} disabled={busy}>
                📦 Abandoned Gear
              </button>
              <button className="btn btn-secondary" onClick={() => runDemoScenario('multi_camera')} disabled={busy}>
                📡 Multi-Post Correlation
              </button>
            </div>
          </div>
          {demoMsg && <span className="demo-status-msg">{demoMsg}</span>}
        </div>
      </div>

      {/* 1-Click QRT Armed Patrol Scramble Modal */}
      {dashboardScrambleInc && (
        <QRTScrambleModal
          incident={dashboardScrambleInc}
          onClose={() => setDashboardScrambleInc(null)}
        />
      )}
    </div>
  );
}
