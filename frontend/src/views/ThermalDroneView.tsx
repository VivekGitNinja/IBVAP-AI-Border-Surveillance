import React, { useState, useEffect } from "react";
import { api } from "../api";
import type { Camera } from "../types";
import { playTacticalTone } from "../utils/audio";
import { WebSocketVideoCanvas } from "../components/WebSocketVideoCanvas";

export function ThermalDroneView({ cameras }: { cameras: Camera[] }) {
  const [palette, setPalette] = useState<'optical' | 'white-hot' | 'black-hot' | 'ironbow' | 'rainbow'>('ironbow');
  const [selectedCamId, setSelectedCamId] = useState<number>(cameras[0]?.id || 1);
  const [alt, setAlt] = useState(148);
  const [speed, setSpeed] = useState(44);
  const [battery, setBattery] = useState(89);
  const [flightMode, setFlightMode] = useState('AUTONOMOUS_ORBIT');
  const [targetLocked, setTargetLocked] = useState(true);

  const selectedCam = cameras.find((c) => c.id === selectedCamId) || cameras[0];

  const shaderClass = `thermal-shader-${palette}`;

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <h1 style={{ margin: 0 }}>Multi-Spectral Colormap & Aerial Reconnaissance Simulator</h1>
            <span style={{ fontSize: 10, padding: '2px 8px', borderRadius: 4, background: '#f59e0b22', color: '#f59e0b', border: '1px solid #f59e0b55', fontWeight: 600 }}>
              SIMULATED TELEMETRY & POST-PROCESS COLORMAP
            </span>
          </div>
          <p style={{ margin: '4px 0 0', fontSize: 12, color: 'var(--text-secondary)' }}>
            Client-side post-process false-color palette visualizer (White-Hot, Black-Hot, Ironbow) applied over optical CCTV feeds with simulated flight HUD telemetry. Not connected to a physical thermal sensor or drone hardware.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button
            className={`btn ${palette === 'optical' ? 'btn-primary' : 'btn-secondary'}`}
            onClick={() => { playTacticalTone('click'); setPalette('optical'); }}
          >
            Daylight Optical
          </button>
          <button
            className={`btn ${palette === 'white-hot' ? 'btn-primary' : 'btn-secondary'}`}
            onClick={() => { playTacticalTone('click'); setPalette('white-hot'); }}
          >
            White-Hot Filter
          </button>
          <button
            className={`btn ${palette === 'black-hot' ? 'btn-primary' : 'btn-secondary'}`}
            onClick={() => { playTacticalTone('click'); setPalette('black-hot'); }}
          >
            Black-Hot Filter
          </button>
          <button
            className={`btn ${palette === 'ironbow' ? 'btn-primary' : 'btn-secondary'}`}
            onClick={() => { playTacticalTone('click'); setPalette('ironbow'); }}
            style={{ background: palette === 'ironbow' ? 'linear-gradient(90deg, #7c3aed, #ea580c)' : undefined }}
          >
            Ironbow Filter
          </button>
          <button
            className={`btn ${palette === 'rainbow' ? 'btn-primary' : 'btn-secondary'}`}
            onClick={() => { playTacticalTone('click'); setPalette('rainbow'); }}
          >
            Rainbow Filter
          </button>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '3fr 1fr', gap: 16 }}>
        {/* Main Thermal Viewport */}
        <div className="thermal-viewport-container" style={{ height: 520 }}>
          {selectedCam ? (
            <img
              src={`/api/v1/cameras/${selectedCam.id}/snapshot?t=${Date.now()}`}
              alt="Thermal Drone Feed"
              className={shaderClass}
              style={{ width: '100%', height: '100%', objectFit: 'cover' }}
            />
          ) : (
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: '#666' }}>
              NO DRONE LINK
            </div>
          )}

          {/* Tactical Crosshair Reticle */}
          <div className="drone-crosshair" />

          {/* UAV OSD HUD Overlay */}
          <div className="drone-osd-overlay">
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <div>
                <b style={{ color: '#00ff9d', fontSize: 13 }}>UAV FALCON-01 // AIRBORNE RECON [SIMULATOR]</b>
                <div style={{ fontSize: 10, color: 'var(--text-ghost)' }}>MODE: {flightMode} • HUD: SIMULATED (NO HARDWARE UAV)</div>
              </div>
              <div style={{ textAlign: 'right' }}>
                <b style={{ color: '#00f0ff' }}>COLORMAP FILTER: {palette.toUpperCase()}</b>
                <div style={{ fontSize: 10, color: '#00ff9d' }}>BATTERY: {battery}% • REMAINING: 38 MINS (SIM)</div>
              </div>
            </div>

            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end' }}>
              <div>
                <div>ALT: <b>{alt}m AGL</b> (SIM)</div>
                <div>SPD: <b>{speed} km/h</b> (SIM)</div>
                <div>HDG: <b>042° NE</b></div>
                <div>PITCH: <b>-32.4°</b></div>
              </div>
              <div style={{ textAlign: 'center' }}>
                {targetLocked && (
                  <div style={{ border: '1px solid #ff2a55', background: 'rgba(255, 42, 85, 0.2)', color: '#ff2a55', padding: '4px 12px', borderRadius: 4, fontWeight: 800, letterSpacing: 2 }}>
                    [ SIMULATED TARGET TRACK ]
                  </div>
                )}
              </div>
              <div style={{ textAlign: 'right' }}>
                <div>LAT: <b>32.7266° N</b></div>
                <div>LON: <b>74.8570° E</b></div>
                <div>GRID: <b>SSB-SEC-A-092</b></div>
                <div>ENCRYPTION: <b>AES-256-GCM</b></div>
              </div>
            </div>
          </div>
        </div>

        {/* Flight Telemetry & Controls */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div className="panel" style={{ margin: 0, padding: 16 }}>
            <h3 style={{ color: '#00f0ff', marginBottom: 12, fontSize: 14 }}>🎮 Drone Mission Vector Controls</h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <button
                className={`btn ${flightMode === 'AUTONOMOUS_ORBIT' ? 'btn-primary' : 'btn-secondary'}`}
                onClick={() => { playTacticalTone('click'); setFlightMode('AUTONOMOUS_ORBIT'); }}
              >
                🔄 Orbit Sector Alpha Perimeter
              </button>
              <button
                className={`btn ${flightMode === 'THERMAL_TRACKING' ? 'btn-primary' : 'btn-secondary'}`}
                onClick={() => { playTacticalTone('click'); setFlightMode('THERMAL_TRACKING'); setTargetLocked(true); }}
              >
                🎯 Lock & Track Heat Signature
              </button>
              <button
                className={`btn ${flightMode === 'CONVOY_ESCORT' ? 'btn-primary' : 'btn-secondary'}`}
                onClick={() => { playTacticalTone('click'); setFlightMode('CONVOY_ESCORT'); }}
              >
                🛡️ Convoy Aerial Escort Route
              </button>
              <button
                className="btn btn-danger"
                onClick={() => { playTacticalTone('escalate'); setFlightMode('RETURN_TO_LAUNCH'); }}
                style={{ marginTop: 6 }}
              >
                ⚡ Return To Launch (RTL)
              </button>
            </div>
          </div>

          <div className="panel" style={{ margin: 0, padding: 16 }}>
            <h3 style={{ color: '#fff', marginBottom: 12, fontSize: 13 }}>Switch Surveillance Camera Sensor</h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {cameras.map((c) => (
                <button
                  key={c.id}
                  className={`btn btn-sm ${selectedCamId === c.id ? 'btn-primary' : 'btn-secondary'}`}
                  style={{ justifyContent: 'flex-start' }}
                  onClick={() => { playTacticalTone('click'); setSelectedCamId(c.id); }}
                >
                  📹 {c.name} ({c.bop})
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ─── Tactical UI Error Boundary ──────────────────────────────── */
class ErrorBoundary extends React.Component<{ children: React.ReactNode }, { hasError: boolean; error: any }> {
  constructor(props: any) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: any) {
    return { hasError: true, error };
  }

  componentDidCatch(error: any, errorInfo: any) {
    console.error("C4ISR Interface Error Caught:", error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          height: '100vh',
          background: '#02060c',
          color: '#e2effc',
          fontFamily: 'monospace',
          padding: 24,
          textAlign: 'center'
        }}>
          <div style={{ color: '#ff2a55', fontSize: 24, fontWeight: 'bold', marginBottom: 12 }}>
            ⚠️ TACTICAL C4ISR CONSOLE RECOVERY
          </div>
          <p style={{ maxWidth: 500, color: 'var(--text-secondary)', marginBottom: 20 }}>
            {String(this.state.error?.message || this.state.error || 'A state synchronization issue occurred.')}
          </p>
          <button
            style={{
              padding: '10px 24px',
              background: '#00f0ff',
              color: '#02060c',
              border: 'none',
              borderRadius: 4,
              fontWeight: 'bold',
              cursor: 'pointer',
              letterSpacing: 1
            }}
            onClick={() => {
              localStorage.removeItem('ibvap_token');
              localStorage.removeItem('token');
              window.location.reload();
            }}
          >
            ↻ RECOVER & RECONNECT CONSOLE
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

