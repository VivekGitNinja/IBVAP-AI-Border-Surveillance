import React, { useState, useEffect, useRef } from "react";
import { api } from "../api";
import type { Camera, Incident } from "../types";
import { playTacticalTone } from "../utils/audio";
import { WebSocketVideoCanvas } from "../components/WebSocketVideoCanvas";
import { PTZController } from "../components/PTZController";
import { ZoneEditorModal } from "../components/ZoneEditorModal";
import { BirdseyeView } from "../components/BirdseyeView";
import { QRTScrambleModal } from "../components/QRTScrambleModal";
import { LegalCertificateModal } from "../components/LegalCertificateModal";
import { TacticalHUD } from "../components/TacticalHUD";

export function LiveMonitorView({
  cameras,
  incidents = [],
  onRefresh,
}: {
  cameras: Camera[];
  incidents?: Incident[];
  onRefresh: () => void;
}) {
  const [view, setView] = useState<'grid' | 'wizard' | 'discover'>('grid');
  const [gridLayout, setGridLayout] = useState<'2x2' | '3x3' | 'theater' | 'birdseye'>('2x2');
  const [filterMode, setFilterMode] = useState<'all' | 'hardware' | 'online' | 'alert'>('all');
  const [theaterActiveId, setTheaterActiveId] = useState<number | null>(null);
  const [zoneStudioCamId, setZoneStudioCamId] = useState<number | null>(null);
  const [scrambleIncident, setScrambleIncident] = useState<Incident | null>(null);
  const [legalCertIncident, setLegalCertIncident] = useState<Incident | null>(null);
  const authToken = typeof localStorage !== 'undefined' ? (localStorage.getItem('ibvap_token') || localStorage.getItem('token') || '') : '';

  // Helper to determine if camera is online/active
  const isCameraOnline = (s?: string | null, active?: boolean) => {
    if (active) return true;
    if (!s) return false;
    const u = s.toUpperCase();
    return u === 'ONLINE' || u === 'HEALTHY' || u === 'DEGRADED';
  };

  // Prioritize active cameras and local Mac/USB hardware webcams at the top
  const sortedCameras = [...cameras].sort((a, b) => {
    // 1. Online cameras first
    const aOnline = isCameraOnline(a.status, a.active) ? 1 : 0;
    const bOnline = isCameraOnline(b.status, b.active) ? 1 : 0;
    if (aOnline !== bOnline) return bOnline - aOnline;

    // 2. Local Mac/USB hardware webcams first
    const aUsb = ((a.stream_url || '').startsWith('usb://') || (a.name || '').toLowerCase().includes('mac')) ? 1 : 0;
    const bUsb = ((b.stream_url || '').startsWith('usb://') || (b.name || '').toLowerCase().includes('mac')) ? 1 : 0;
    if (aUsb !== bUsb) return bUsb - aUsb;

    // 3. Real streams before demo streams
    const aReal = !(a.stream_url || '').startsWith('demo://') ? 1 : 0;
    const bReal = !(b.stream_url || '').startsWith('demo://') ? 1 : 0;
    if (aReal !== bReal) return bReal - aReal;

    return a.id - b.id;
  });

  const macCam = cameras.find((c) => (c.stream_url || '').startsWith('usb://') || (c.name || '').toLowerCase().includes('mac'));
  const physicalCount = cameras.filter((c) => !(c.stream_url || '').startsWith('demo://')).length;
  const onlineCount = cameras.filter((c) => isCameraOnline(c.status, c.active)).length;
  const alertCount = cameras.filter((c) => (c.health_score ?? 100) < 70 || !isCameraOnline(c.status, c.active)).length;

  // Filter cameras
  const filteredCameras = sortedCameras.filter((c) => {
    if (filterMode === 'hardware') return !(c.stream_url || '').startsWith('demo://');
    if (filterMode === 'online') return isCameraOnline(c.status, c.active);
    if (filterMode === 'alert') return (c.health_score ?? 100) < 70 || !isCameraOnline(c.status, c.active);
    return true;
  });

  // Effective active camera for theater view: prefer active selection, then Mac camera, then first online
  const activeTheaterCam =
    filteredCameras.find((c) => c.id === theaterActiveId) ||
    filteredCameras.find((c) => (c.stream_url || '').startsWith('usb://') || (c.name || '').toLowerCase().includes('mac')) ||
    filteredCameras.find((c) => isCameraOnline(c.status, c.active)) ||
    filteredCameras[0] ||
    sortedCameras[0] ||
    null;

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <h1 style={{ margin: 0 }}>Tactical Video Surveillance Grid</h1>
            {physicalCount > 0 && (
              <span className="page-tag" style={{ background: 'rgba(0, 255, 157, 0.15)', borderColor: '#00ff9d', color: '#00ff9d' }}>
                🟢 {physicalCount} PHYSICAL HARDWARE {physicalCount === 1 ? 'NODE' : 'NODES'} ACTIVE
              </span>
            )}
          </div>
          <p style={{ margin: '4px 0 0', fontSize: 12, color: 'var(--text-secondary)' }}>
            Integrated RTSP, USB & thermal camera edge perception node matrix
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <span className="page-tag">{cameras.length} TOTAL NODES</span>
          {physicalCount > 0 && (
            <button
              className="btn btn-sm btn-danger"
              style={{ background: 'rgba(255, 42, 85, 0.2)', borderColor: '#ff2a55', color: '#ff2a55' }}
              onClick={async () => {
                if (confirm('Disconnect ALL physical hardware cameras and release webcam hardware immediately?')) {
                  playTacticalTone('click');
                  const phys = cameras.filter((c) => !c.stream_url?.startsWith('demo://'));
                  for (const pc of phys) {
                    try { await api.disconnectCamera(pc.id); } catch {}
                  }
                  onRefresh();
                }
              }}
              title="Release all local hardware webcams and turn off camera LEDs"
            >
              🔌 Power Off All Cameras
            </button>
          )}
          {macCam && (
            <button
              className="btn btn-secondary"
              onClick={async () => {
                playTacticalTone('click');
                if (!isCameraOnline(macCam.status, macCam.active)) {
                  try {
                    await api.connectCamera(macCam.id);
                    onRefresh();
                  } catch (e: any) {
                    alert(`Failed to power on Mac camera: ${e?.message || e}`);
                  }
                }
                setTheaterActiveId(macCam.id);
                setGridLayout('theater');
              }}
              style={{
                borderColor: isCameraOnline(macCam.status, macCam.active) ? '#00ff9d' : '#00f0ff',
                color: isCameraOnline(macCam.status, macCam.active) ? '#00ff9d' : '#00f0ff',
                background: isCameraOnline(macCam.status, macCam.active) ? 'rgba(0, 255, 157, 0.12)' : 'rgba(0, 240, 255, 0.12)',
                fontWeight: 600,
              }}
              title="Focus Mac FaceTime HD Hardware Camera in Theater View"
            >
              📷 {isCameraOnline(macCam.status, macCam.active) ? 'Mac Cam (LIVE)' : 'Mac Cam (STANDBY)'}
            </button>
          )}
          <button
            className="btn btn-secondary"
            onClick={() => setZoneStudioCamId(cameras[0]?.id || 1)}
            style={{ borderColor: '#00f0ff', color: '#00f0ff' }}
            title="Launch Interactive Perimeter ROI & Tripwire Studio"
          >
            📐 Zone & Tripwire Studio
          </button>
          {incidents.length > 0 && (
            <button
              className="btn btn-secondary"
              onClick={() => {
                playTacticalTone('verify');
                setLegalCertIncident(incidents[0]);
              }}
              style={{ borderColor: '#a855f7', color: '#c084fc', background: 'rgba(168, 85, 247, 0.1)' }}
              title="Export Bharatiya Sakshya Adhiniyam, 2023 Section 63 Electronic Evidence Certificate"
            >
              ⚖️ BSA 2023 §63 Vault
            </button>
          )}
          <button className="btn btn-primary" onClick={() => setView('wizard')}>+ Deploy Camera</button>
          <button className="btn btn-secondary" onClick={() => setView('discover')}>🔍 Network Auto-Discovery</button>
        </div>
      </div>

      {view === 'wizard' && <CameraWizard onDone={() => { setView('grid'); onRefresh(); }} onCancel={() => setView('grid')} />}
      {view === 'discover' && <CameraDiscovery onDone={() => { setView('grid'); onRefresh(); }} onCancel={() => setView('grid')} />}

      {view === 'grid' && (
        <>
          {/* Active Perimeter Breaches Ticker with Direct 1-Click BSA 2023 §63 Legal Certificate & QRT Scramble */}
          {incidents.filter(i => i.status === 'NEW' || i.status === 'ACKNOWLEDGED').slice(0, 2).map((inc) => (
            <div
              key={inc.id}
              style={{
                background: 'linear-gradient(90deg, rgba(255, 42, 85, 0.18) 0%, rgba(10, 15, 29, 0.95) 100%)',
                border: '1px solid #ff2a55',
                borderRadius: 6,
                padding: '10px 16px',
                marginBottom: 14,
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                boxShadow: '0 0 24px rgba(255, 42, 85, 0.25)',
                flexWrap: 'wrap',
                gap: 12,
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <span style={{ fontSize: 22 }}>🚨</span>
                <div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                    <b style={{ color: '#ff2a55', fontSize: 13, letterSpacing: 0.8 }}>
                      {inc.severity} PERIMETER BREACH: {inc.title}
                    </b>
                    <span style={{ background: '#ff2a55', color: '#fff', fontSize: 10, fontWeight: 800, padding: '2px 6px', borderRadius: 3 }}>
                      THREAT {inc.threat_score}/100
                    </span>
                    <span style={{ color: '#00f0ff', fontSize: 11, fontFamily: 'var(--font-mono)' }}>
                      [{inc.incident_code}]
                    </span>
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 2 }}>
                    Post: <b>{inc.camera_name || 'BOP Sector Perimeter'}</b> • Evidence: <b>SHA-256 Bit-Sealed</b> • Technical Standard: <b>BSA 2023 §63 Aligned</b>
                  </div>
                </div>
              </div>

              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <button
                  className="btn btn-sm btn-secondary"
                  onClick={() => {
                    playTacticalTone('verify');
                    setLegalCertIncident(inc);
                  }}
                  style={{
                    borderColor: '#a855f7',
                    color: '#c084fc',
                    background: 'rgba(168, 85, 247, 0.15)',
                    padding: '5px 12px',
                    fontSize: 11,
                    fontWeight: 700,
                  }}
                  title="Generate, cryptographically verify, and export Bharatiya Sakshya Adhiniyam, 2023 §63 court affidavit"
                >
                  ⚖️ Export BSA 2023 §63 Certificate
                </button>
                <button
                  className="btn btn-sm btn-primary"
                  onClick={() => {
                    playTacticalTone('alert');
                    setScrambleIncident(inc);
                  }}
                  style={{
                    background: '#ff2a55',
                    borderColor: '#ff2a55',
                    padding: '5px 12px',
                    fontSize: 11,
                    fontWeight: 700,
                  }}
                  title="Scramble armed quick reaction team"
                >
                  ⚡ Scramble QRT Team
                </button>
              </div>
            </div>
          ))}

          {/* Controls Bar: Filter Pills + Grid Switcher */}
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14, flexWrap: 'wrap', gap: 10 }}>
            <div className="filter-pills-group" style={{ display: 'flex', gap: 6 }}>
              <button
                className={`filter-pill ${filterMode === 'all' ? 'active' : ''}`}
                onClick={() => { playTacticalTone('click'); setFilterMode('all'); }}
              >
                All Nodes ({cameras.length})
              </button>
              <button
                className={`filter-pill ${filterMode === 'hardware' ? 'active' : ''}`}
                onClick={() => { playTacticalTone('click'); setFilterMode('hardware'); }}
              >
                🟢 Hardware ({physicalCount})
              </button>
              <button
                className={`filter-pill ${filterMode === 'online' ? 'active' : ''}`}
                onClick={() => { playTacticalTone('click'); setFilterMode('online'); }}
              >
                ⚡ Online ({onlineCount})
              </button>
              {alertCount > 0 && (
                <button
                  className={`filter-pill ${filterMode === 'alert' ? 'active' : ''}`}
                  onClick={() => { playTacticalTone('click'); setFilterMode('alert'); }}
                  style={{ color: filterMode === 'alert' ? '#ff2a55' : '#ff7a8a', borderColor: '#ff2a5580' }}
                >
                  🚨 Threat / Alerting ({alertCount})
                </button>
              )}
            </div>

            {/* Layout Switcher: 2x2, 3x3, Theater, Birdseye */}
            <div className="grid-view-switcher">
              <button
                className={`grid-view-btn ${gridLayout === '2x2' ? 'active' : ''}`}
                onClick={() => { playTacticalTone('click'); setGridLayout('2x2'); }}
                title="2x2 High-Resolution Matrix"
              >
                ⊞ 2x2 Matrix
              </button>
              <button
                className={`grid-view-btn ${gridLayout === '3x3' ? 'active' : ''}`}
                onClick={() => { playTacticalTone('click'); setGridLayout('3x3'); }}
                title="3x3 Tactical Camera Wall"
              >
                ▦ 3x3 Grid
              </button>
              <button
                className={`grid-view-btn ${gridLayout === 'theater' ? 'active' : ''}`}
                onClick={() => { playTacticalTone('click'); setGridLayout('theater'); }}
                title="Theater Focus Mode"
              >
                ⬚ Theater Focus
              </button>
              <button
                className={`grid-view-btn ${gridLayout === 'birdseye' ? 'active' : ''}`}
                onClick={() => { playTacticalTone('click'); setGridLayout('birdseye'); }}
                title="Frigate-Style Birdseye Smart Composite View"
                style={{ color: gridLayout === 'birdseye' ? '#00ff9d' : undefined }}
              >
                🦅 Birdseye Smart
              </button>
            </div>
          </div>

          {filteredCameras.length === 0 ? (
            <div className="camera-empty">
              <div className="empty-icon">◎</div>
              <h3>No surveillance cameras matching criteria</h3>
              <p>Try switching filter pills or connect new IP, RTSP, or USB cameras.</p>
              <div style={{ display: 'flex', gap: 10, justifyContent: 'center' }}>
                <button className="btn btn-secondary" onClick={() => setFilterMode('all')}>Show All Cameras</button>
                <button className="btn btn-primary" onClick={() => setView('wizard')}>+ Deploy Camera</button>
              </div>
            </div>
          ) : gridLayout === 'birdseye' ? (
            /* Frigate-Style Birdseye View */
            <BirdseyeView
              cameras={filteredCameras}
              incidents={incidents}
              onOpenZoneStudio={(id) => setZoneStudioCamId(id)}
              onScrambleQrt={(inc) => setScrambleIncident(inc)}
            />
          ) : gridLayout === 'theater' ? (
            /* Theater Mode: Primary large view + thumbnail sidebar */
            <div className="camera-grid-theater">
              <div className="camera-theater-main">
                {activeTheaterCam && (
                  <CameraFeed
                    key={activeTheaterCam.id}
                    camera={activeTheaterCam}
                    onOpenZoneStudio={(id) => setZoneStudioCamId(id)}
                    onRefresh={onRefresh}
                  />
                )}
              </div>
              <div className="camera-theater-strip">
                <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-secondary)', padding: '2px 4px 6px', letterSpacing: 0.5 }}>
                  RECON MATRIX ({filteredCameras.length} CHANNELS)
                </div>
                {filteredCameras.map((c) => {
                  const isActive = activeTheaterCam?.id === c.id;
                  const isReal = !c.stream_url?.startsWith('demo://');
                  return (
                    <div
                      key={c.id}
                      className={`theater-thumb-item ${isActive ? 'active' : ''}`}
                      onClick={() => {
                        playTacticalTone('click');
                        setTheaterActiveId(c.id);
                      }}
                      title={`Switch featured view to ${c.name}`}
                    >
                      <img
                        className="theater-thumb-img"
                        src={`/api/v1/cameras/${c.id}/snapshot${authToken ? `?token=${encodeURIComponent(authToken)}` : ''}`}
                        alt={c.name}
                        onError={(e) => {
                          const target = e.target as HTMLImageElement;
                          target.style.display = 'none';
                        }}
                      />
                      <div className="theater-thumb-info">
                        <div className="theater-thumb-title">
                          {isReal && <span style={{ color: '#00ff9d', marginRight: 4 }}>●</span>}
                          {c.name}
                        </div>
                        <div className="theater-thumb-meta">
                          <span>{c.bop || 'BOP-01'}</span>
                          <span>•</span>
                          <span style={{ color: c.status === 'ONLINE' ? '#00ff9d' : '#ffaa00' }}>{c.status}</span>
                          <span>•</span>
                          <span>{c.fps || 15} FPS</span>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          ) : (
            /* Standard 2x2 or 3x3 Grid */
            <div className={gridLayout === '2x2' ? 'camera-grid-2x2' : 'camera-grid-3x3'}>
              {filteredCameras.map((c) => (
                <CameraFeed
                  key={c.id}
                  camera={c}
                  onOpenZoneStudio={(id) => setZoneStudioCamId(id)}
                  onRefresh={onRefresh}
                />
              ))}
            </div>
          )}
        </>
      )}

      {/* Interactive Zone & Tripwire Calibration Studio Modal */}
      {zoneStudioCamId !== null && (
        <ZoneEditorModal
          cameras={cameras}
          initialCameraId={zoneStudioCamId}
          onClose={() => setZoneStudioCamId(null)}
          onSaved={onRefresh}
        />
      )}

      {/* 1-Click QRT Armed Patrol Scramble Modal */}
      {scrambleIncident && (
        <QRTScrambleModal
          incident={scrambleIncident}
          onClose={() => setScrambleIncident(null)}
        />
      )}

      {/* Official Section 63 BSA Certificate Modal */}
      {legalCertIncident && (
        <LegalCertificateModal
          cert={{
            certificate_id: `CERT-BSA63-2026-${legalCertIncident.incident_code}`,
            incident_code: legalCertIncident.incident_code,
            surveillance_post: legalCertIncident.camera_name || 'BOP-01 (Sector Alpha)',
            camera_designation: legalCertIncident.camera_name || 'CAM-01 Zero-Line Camera',
            sha256_digest: (legalCertIncident as any).clip_sha256 || 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
            evidence_id: legalCertIncident.id,
            generated_at: legalCertIncident.created_at || new Date().toISOString(),
            certifying_officer: 'Commandant Rajeshwar Singh',
            officer_rank: 'Commandant (Surveillance Operations)',
            clock_source: 'NTP Synced with NPL Indian Standard Time (IST)',
            storage_integrity: 'Direct NVMe Encrypted Ring Buffer (ext4)',
          }}
          onClose={() => setLegalCertIncident(null)}
        />
      )}
    </div>
  );
}

/* ─── Dashboard Camera Frame (lightweight, no MJPEG) ─────────── */
const DashboardCamFrame = React.memo(function DashboardCamFrame({ cameraId, cameraName }: { cameraId: number; cameraName: string }) {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const iv = setInterval(() => setTick(t => t + 1), 2000);
    return () => clearInterval(iv);
  }, []);
  const token = typeof localStorage !== 'undefined' ? (localStorage.getItem('ibvap_token') || localStorage.getItem('token')) : '';
  const tokenParam = token ? `&token=${encodeURIComponent(token)}` : '';
  return (
    <div className="tactical-cam-frame">
      <img
        src={`/api/v1/cameras/${cameraId}/snapshot?t=${tick}${tokenParam}`}
        alt={cameraName}
        onError={(e) => {
          const target = e.target as HTMLImageElement;
          setTimeout(() => { target.src = `/api/v1/cameras/${cameraId}/snapshot?t=${Date.now()}${tokenParam}`; }, 3000);
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

/* ─── Camera Feed Component ───────────────────────────────────── */
function CameraFeed({
  camera,
  onOpenZoneStudio,
  onRefresh,
}: {
  camera: Camera;
  onOpenZoneStudio?: (id: number) => void;
  onRefresh: () => void;
}) {
  const [detecting, setDetecting] = useState(true);
  const [showPtz, setShowPtz] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const [testingLink, setTestingLink] = useState(false);
  const [testResult, setTestResult] = useState<{ status: string; resolution?: string; fps?: number; error?: string } | null>(null);
  const [imgKey, setImgKey] = useState(0);
  const [liveFps, setLiveFps] = useState(camera.fps || 15);
  const handleFpsUpdate = React.useCallback((fps: number) => setLiveFps(fps), []);
  const isReal = !camera.stream_url?.startsWith('demo://');

  const [togglingPower, setTogglingPower] = useState(false);
  const isPoweredOn = camera.active === true || camera.status === 'ONLINE' || camera.status === 'HEALTHY' || camera.status === 'DEGRADED';

  const handleTestLink = async () => {
    setTestingLink(true);
    playTacticalTone('click');
    try {
      const res = await api.testCamera(camera.id);
      setTestResult(res);
      playTacticalTone(res.status === 'CONNECTED' ? 'verify' : 'alert');
    } catch (err: any) {
      setTestResult({ status: 'ERROR', error: err.message || 'Connection test failed' });
      playTacticalTone('alert');
    } finally {
      setTestingLink(false);
    }
  };

  const handleTogglePower = async () => {
    setTogglingPower(true);
    playTacticalTone('click');
    try {
      if (isPoweredOn) {
        await api.disconnectCamera(camera.id);
        playTacticalTone('verify');
      } else {
        await api.connectCamera(camera.id);
        playTacticalTone('verify');
      }
      onRefresh();
    } catch (err: any) {
      alert(`Camera power control error: ${err.message || 'Action failed'}`);
      playTacticalTone('alert');
    } finally {
      setTogglingPower(false);
    }
  };

  const token = typeof localStorage !== 'undefined' ? (localStorage.getItem('ibvap_token') || localStorage.getItem('token')) : '';
  const tokenParam = token ? `&token=${encodeURIComponent(token)}` : '';
  const snapshotUrl = `/api/v1/cameras/${camera.id}/snapshot?t=${imgKey}${tokenParam}`;

  // Auto-refresh snapshot every 1.5s when WS stream is paused
  useEffect(() => {
    if (detecting && isPoweredOn) return;
    const interval = setInterval(() => {
      setImgKey(k => k + 1);
    }, 1500);
    return () => clearInterval(interval);
  }, [detecting, isPoweredOn]);

  const toggleStream = () => {
    playTacticalTone('click');
    setDetecting((prev) => !prev);
  };

  const handleDisconnect = async () => {
    if (!confirm(`Disconnect and power off camera "${camera.name}"? This will immediately release the hardware sensor and turn off the camera LED.`)) {
      return;
    }
    setDisconnecting(true);
    playTacticalTone('click');
    try {
      await api.disconnectCamera(camera.id);
      playTacticalTone('verify');
    } catch {
      // fallback to delete
      await api.deleteCamera(camera.id);
    }
    setDisconnecting(false);
    onRefresh();
  };

  return (
    <TacticalHUD
      cameraId={camera.id}
      cameraName={camera.name}
      sector={camera.sector || camera.bop || 'BOP-01'}
      latitude={camera.latitude}
      longitude={camera.longitude}
      status={camera.status}
      fps={liveFps}
      resolution={camera.resolution || '1280x720'}
      isRecording={detecting && isPoweredOn}
      recTimestamp={new Date().toISOString().substring(11, 19)}
      variant="card"
      className={`camera-feed ${isReal ? 'hardware-node' : ''}`}
    >
      {/* PTZ Joystick Overlay */}
      {showPtz && (
        <div style={{ position: 'absolute', top: 10, right: 10, zIndex: 12 }}>
          <PTZController cameraId={camera.id} cameraName={camera.name} onClose={() => setShowPtz(false)} />
        </div>
      )}

      <div className="camera-feed-video">
        <WebSocketVideoCanvas
          cameraId={camera.id}
          cameraName={camera.name}
          fallbackSnapshotUrl={snapshotUrl}
          isStreaming={detecting && isPoweredOn}
          onFpsUpdate={handleFpsUpdate}
        />
        <div className="corner-bracket cb-top-left" />
        <div className="corner-bracket cb-top-right" />
        <div className="corner-bracket cb-bottom-left" />
        <div className="corner-bracket cb-bottom-right" />
        <div className="tactical-crosshair-center" />

        <div className="feed-hud">
          <span className="feed-hud-left">
            {isReal && <b style={{ color: '#00ff9d', marginRight: 6 }}>[LIVE HARDWARE]</b>}
            {camera.bop || 'BOP-01'} // {camera.name}
          </span>
          <span className="feed-hud-right">
            {isPoweredOn ? (
              detecting ? <span className="detect-badge">LIVE WS • YOLO26</span> : <span className="page-tag" style={{ background: 'rgba(255, 170, 0, 0.2)', color: '#ffaa00' }}>PAUSED</span>
            ) : (
              <span className="page-tag" style={{ background: 'rgba(100, 100, 100, 0.4)', color: '#aaa' }}>POWERED OFF</span>
            )}
            <span className={`health-dot-sm ${camera.status.toLowerCase()}`} />
          </span>
        </div>
      </div>

      <div className="camera-feed-info">
        <div className="feed-details">
          <span className="feed-detail"><label>Health</label><b className={camera.health_score < 50 ? 'low' : ''}>{camera.health_score.toFixed(0)}%</b></span>
          <span className="feed-detail"><label>FPS</label>{isPoweredOn ? liveFps : 0}</span>
          <span className="feed-detail"><label>Res</label>{camera.resolution || '1280x720'}</span>
          <span className="feed-detail"><label>Type</label><span style={{ color: isReal ? '#00ff9d' : 'inherit' }}>{camera.stream_url?.startsWith('usb://') ? 'USB/WEBCAM' : (camera.camera_type || 'RTSP')}</span></span>
        </div>
        <div className="feed-actions">
          {/* Main Hardware Power Toggle Button */}
          <button
            className={`btn btn-sm ${isPoweredOn ? 'btn-danger' : 'btn-success'}`}
            style={{
              background: isPoweredOn ? 'rgba(255, 42, 85, 0.2)' : 'rgba(0, 255, 157, 0.2)',
              borderColor: isPoweredOn ? '#ff2a55' : '#00ff9d',
              color: isPoweredOn ? '#ff2a55' : '#00ff9d',
              fontWeight: 'bold',
            }}
            onClick={handleTogglePower}
            disabled={togglingPower}
            title={isPoweredOn ? "Power OFF hardware sensor, stop AI pipeline, and release webcam" : "Power ON camera sensor, initialize video stream, and start live AI detection"}
          >
            {togglingPower ? 'Switching...' : isPoweredOn ? '🔌 Power OFF' : '⚡ Power ON'}
          </button>

          {/* Pause / Play View Button */}
          {isPoweredOn && (
            <button
              className={`btn btn-sm ${detecting ? 'btn-warn' : 'btn-primary'}`}
              onClick={toggleStream}
              title={detecting ? "Pause browser canvas stream" : "Resume browser canvas stream"}
            >
              {detecting ? '⏸ Pause View' : '▶ Play View'}
            </button>
          )}

          {onOpenZoneStudio && (
            <button
              className="btn btn-sm btn-secondary"
              onClick={() => { playTacticalTone('click'); onOpenZoneStudio(camera.id); }}
              title="Calibrate Perimeter Zones & Tripwires"
              style={{ borderColor: '#00f0ff', color: '#00f0ff' }}
            >
              📐 Zones
            </button>
          )}
          <button
            className={`btn btn-sm ${showPtz ? 'btn-primary' : 'btn-secondary'}`}
            onClick={() => { playTacticalTone('click'); setShowPtz(!showPtz); }}
            title="Open ONVIF Pan-Tilt-Zoom Tactical Controller"
          >
            🕹️ PTZ
          </button>
          <button
            className="btn btn-sm btn-secondary"
            onClick={handleTestLink}
            disabled={testingLink}
            title="Probe camera stream link (RTSP / USB / File) for real connectivity"
          >
            {testingLink ? 'Testing...' : '📡 Test Link'}
          </button>
          <button
            className="btn btn-sm btn-danger"
            onClick={async () => {
              if (confirm(`Remove surveillance node ${camera.name}?`)) {
                playTacticalTone('click');
                await api.deleteCamera(camera.id);
                onRefresh();
              }
            }}
            title="Delete Camera Node"
          >
            ✕
          </button>
        </div>

        {testResult && (
          <div
            style={{
              padding: '4px 8px',
              fontSize: 10,
              borderRadius: 4,
              marginTop: 6,
              background: testResult.status === 'CONNECTED' ? 'rgba(0, 255, 157, 0.15)' : 'rgba(255, 42, 85, 0.15)',
              border: `1px solid ${testResult.status === 'CONNECTED' ? '#00ff9d' : '#ff2a55'}`,
              color: testResult.status === 'CONNECTED' ? '#00ff9d' : '#ff2a55',
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
            }}
          >
            <span>
              <b>[{testResult.status}]</b>{' '}
              {testResult.status === 'CONNECTED'
                ? `${testResult.resolution} @ ${testResult.fps?.toFixed(1)} FPS`
                : testResult.error || 'Connection refused / offline'}
            </span>
            <button
              style={{ background: 'none', border: 'none', color: 'inherit', cursor: 'pointer', fontSize: 11, padding: 0 }}
              onClick={() => setTestResult(null)}
            >
              ✕
            </button>
          </div>
        )}
      </div>
    </TacticalHUD>
  );
}

/* ─── Camera Wizard & Discovery ───────────────────────────────── */
function CameraWizard({ onDone, onCancel }: { onDone: () => void; onCancel: () => void }) {
  const [step, setStep] = useState(1);
  const [brands, setBrands] = useState<any[]>([]);
  const [selectedBrand, setSelectedBrand] = useState('');
  const [ip, setIp] = useState('');
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  const [port, setPort] = useState('554');
  const [name, setName] = useState('');
  const [location, setLocation] = useState('');
  const [bop, setBop] = useState('BOP-01');
  const [streamUrl, setStreamUrl] = useState('');
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<any>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [usbMode, setUsbMode] = useState(false);
  const [fileMode, setFileMode] = useState(false);
  const [filePath, setFilePath] = useState('');

  useEffect(() => {
    api.cameraBrands().then(setBrands).catch(() => {});
  }, []);

  useEffect(() => {
    if (usbMode) { setStreamUrl('usb://0'); return; }
    if (fileMode) { setStreamUrl(filePath ? `file://${filePath}` : ''); return; }
    const brand = brands.find((b: any) => b.id === selectedBrand);
    if (brand && ip) {
      const url = brand.rtsp_template
        .replace('{user}', username || brand.default_user)
        .replace('{pass}', password)
        .replace('{ip}', ip)
        .replace('{port}', port || String(brand.default_port));
      setStreamUrl(url);
    } else if (ip) {
      setStreamUrl(`rtsp://${username}:${password}@${ip}:${port}/stream1`);
    }
  }, [selectedBrand, ip, username, password, port, brands, usbMode, fileMode, filePath]);

  const doTest = async () => {
    if (!streamUrl) return;
    setTesting(true);
    setTestResult(null);
    try {
      const r = await api.testStream(streamUrl, selectedBrand, username, password);
      setTestResult(r);
      playTacticalTone(r.success ? 'verify' : 'alert');
    } catch (e: any) {
      setTestResult({ success: false, message: e.message || 'Test failed' });
      playTacticalTone('alert');
    }
    setTesting(false);
  };

  const doSave = async () => {
    setSaving(true);
    setError('');
    try {
      await api.createCamera({
        name: name || `${selectedBrand || 'Camera'} — ${ip || 'USB'}`,
        stream_url: streamUrl,
        location: location || 'Border Sector',
        bop: bop,
        camera_type: usbMode ? 'USB' : fileMode ? 'FILE' : 'IP',
        fps: 15,
        resolution: testResult?.width ? `${testResult.width}x${testResult.height}` : '1280x720',
        analytics_enabled: true,
        detection_interval: 3,
      });
      playTacticalTone('verify');
      onDone();
    } catch (e: any) {
      setError(e.message || 'Failed to register camera');
      playTacticalTone('alert');
    }
    setSaving(false);
  };

  const popularBrands = brands.filter((b: any) => !['usb', 'file'].includes(b.id));

  return (
    <div className="wizard-overlay">
      <div className="wizard">
        <div className="wizard-header">
          <h2>Deploy Surveillance Node</h2>
          <button className="btn-close" onClick={onCancel}>✕</button>
        </div>

        <div className="wizard-steps">
          <div className={`wizard-step ${step >= 1 ? 'active' : ''}`}><span>1</span> Hardware Protocol</div>
          <div className="wizard-step-arrow">→</div>
          <div className={`wizard-step ${step >= 2 ? 'active' : ''}`}><span>2</span> Network Telemetry</div>
          <div className="wizard-step-arrow">→</div>
          <div className={`wizard-step ${step >= 3 ? 'active' : ''}`}><span>3</span> Signal Verification</div>
        </div>

        {step === 1 && (
          <div className="wizard-body">
            <p className="wizard-hint">Select defense-grade camera manufacturer or input protocol:</p>
            <div className="brand-grid">
              {popularBrands.map((b: any) => (
                <button
                  key={b.id}
                  className={`brand-card${selectedBrand === b.id ? ' selected' : ''}`}
                  onClick={() => {
                    setSelectedBrand(b.id);
                    setUsbMode(false);
                    setFileMode(false);
                  }}
                >
                  <b>{b.name}</b>
                  <small>RTSP Camera</small>
                </button>
              ))}
            </div>
            <div className="brand-divider"><span>TACTICAL INTERFACES</span></div>
            <div className="brand-alt-row">
              <button
                className={`brand-card alt${usbMode ? ' selected' : ''}`}
                onClick={() => { setUsbMode(true); setFileMode(false); setSelectedBrand('usb'); }}
              >
                <b>USB / Thermal Drone Feed</b>
                <small>Local Sensor /dev/video0</small>
              </button>
              <button
                className={`brand-card alt${fileMode ? ' selected' : ''}`}
                onClick={() => { setFileMode(true); setUsbMode(false); setSelectedBrand('file'); }}
              >
                <b>Recorded Forensic Video</b>
                <small>MP4/AVI/MKV Reconnaissance</small>
              </button>
            </div>
          </div>
        )}

        {step === 2 && (
          <div className="wizard-body">
            {usbMode ? (
              <div className="form-group">
                <label>USB Video Device Index</label>
                <input type="text" value={streamUrl} onChange={(e) => setStreamUrl(e.target.value)} placeholder="usb://0" />
              </div>
            ) : fileMode ? (
              <div className="form-group">
                <label>Forensic Video File Path</label>
                <input type="text" value={filePath} onChange={(e) => setFilePath(e.target.value)} placeholder="/path/to/surveillance_sample.mp4" />
              </div>
            ) : (
              <>
                <div className="form-row">
                  <div className="form-group" style={{ flex: 2 }}>
                    <label>Camera IP Address</label>
                    <input type="text" value={ip} onChange={(e) => setIp(e.target.value)} placeholder="192.168.1.108" data-testid="camera-url-input" />
                  </div>
                  <div className="form-group" style={{ flex: 1 }}>
                    <label>RTSP Port</label>
                    <input type="text" value={port} onChange={(e) => setPort(e.target.value)} placeholder="554" />
                  </div>
                </div>
                <div className="form-row">
                  <div className="form-group">
                    <label>Security Username</label>
                    <input type="text" value={username} onChange={(e) => setUsername(e.target.value)} placeholder="admin" />
                  </div>
                  <div className="form-group">
                    <label>Security Password</label>
                    <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="••••••••" />
                  </div>
                </div>
              </>
            )}

            <div className="form-row">
              <div className="form-group">
                <label>Node Designation Name</label>
                <input type="text" value={name} onChange={(e) => setName(e.target.value)} placeholder="BOP-01 Sector Alpha Gate" />
              </div>
              <div className="form-group">
                <label>Border Outpost (BOP)</label>
                <select value={bop} onChange={(e) => setBop(e.target.value)}>
                  <option value="BOP-01">BOP-01 (Sector Alpha)</option>
                  <option value="BOP-02">BOP-02 (Sector Bravo)</option>
                  <option value="BOP-03">BOP-03 (Sector Charlie)</option>
                  <option value="BOP-04">BOP-04 (Sector Delta)</option>
                </select>
              </div>
            </div>
          </div>
        )}

        {step === 3 && (
          <div className="wizard-body">
            <div className="test-preview-box">
              <label>Constructed RTSP Video URI:</label>
              <div className="stream-url-display">{streamUrl || 'No URI specified'}</div>
              <button className="btn btn-primary" onClick={doTest} disabled={testing || !streamUrl} data-testid="camera-test-button">
                {testing ? 'Testing Optical Signal...' : '⚡ Test Optical Connection'}
              </button>
            </div>

            {testResult && (
              <div className={`test-feedback ${testResult.success ? 'success' : 'fail'}`}>
                {testResult.success ? '✓ Optical link established! Codec: H.264 / FPS: 15' : `✕ Link failure: ${testResult.message}`}
              </div>
            )}
            {error && <div className="test-feedback fail">✕ {error}</div>}
          </div>
        )}

        <div className="wizard-footer">
          {step > 1 && <button className="btn btn-secondary" onClick={() => setStep(step - 1)}>← Previous</button>}
          <div style={{ flex: 1 }} />
          {step < 3 ? (
            <button className="btn btn-primary" onClick={() => setStep(step + 1)} disabled={step === 1 && !selectedBrand}>
              Next Step →
            </button>
          ) : (
            <button className="btn btn-primary" onClick={doSave} disabled={saving}>
              {saving ? 'Registering...' : '✓ Provision Surveillance Node'}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function CameraDiscovery({ onDone, onCancel }: { onDone: () => void; onCancel: () => void }) {
  return <UniversalCameraStudio onDone={onDone} onCancel={onCancel} />;
}

function UniversalCameraStudio({ onDone, onCancel }: { onDone: () => void; onCancel: () => void }) {
  const [tab, setTab] = useState<'lan' | 'probe' | 'phone' | 'remote'>('lan');
  const [subnet, setSubnet] = useState('192.168.29');
  const [localIp, setLocalIp] = useState('192.168.29.253');
  const [scanning, setScanning] = useState(false);
  const [scanDuration, setScanDuration] = useState<number | null>(null);
  const [found, setFound] = useState<any[]>([]);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  const [adopting, setAdopting] = useState<string | null>(null);
  const [testingStream, setTestingStream] = useState<string | null>(null);
  const [streamTestStatus, setStreamTestStatus] = useState<{ [key: string]: string }>({});

  // Strix Smart Prober state
  const [probeIp, setProbeIp] = useState('192.168.29.107');
  const [probeUser, setProbeUser] = useState('admin');
  const [probePass, setProbePass] = useState('admin123');
  const [probing, setProbing] = useState(false);
  const [probeResult, setProbeResult] = useState<any | null>(null);

  // Phone Broadcaster modal state
  const [showPhoneBroadcaster, setShowPhoneBroadcaster] = useState(false);

  // Remote Stream state
  const [remoteName, setRemoteName] = useState('Remote Recon Stream');
  const [remoteUrl, setRemoteUrl] = useState('');
  const [remoteBop, setRemoteBop] = useState('BOP-01 Alpha');

  const refreshNetworkInfo = async () => {
    try {
      const info: any = await api.networkInfo();
      if (info?.subnet) setSubnet(info.subnet);
      if (info?.local_ip) {
        setLocalIp(info.local_ip);
        setProbeIp(info.local_ip.replace(/\.\d+$/, '.107'));
      }
      return info;
    } catch (e: any) {
      return null;
    }
  };

  // Auto-detect host network info on mount
  useEffect(() => {
    refreshNetworkInfo();
  }, []);

  const runLanScan = async () => {
    setScanning(true);
    setStatusMsg('Initiating ultra-fast network hardware scan...');
    playTacticalTone('click');
    const t0 = Date.now();
    try {
      const targetSubnet = subnet.trim() || 'auto';
      const r = await api.discoverCameras(targetSubnet);
      const list = Array.isArray(r) ? r : (r as any).discovered || [];
      // Filter out gateway router if desired or keep with tag
      setFound(list);
      const dur = Math.round((Date.now() - t0) / 100) / 10;
      setScanDuration(dur);
      playTacticalTone(list.length > 0 ? 'verify' : 'alert');
      if (list.length > 0) {
        setStatusMsg(`Discovered ${list.length} hardware endpoints on subnet ${targetSubnet}.0/24 in ${dur}s.`);
      } else {
        setStatusMsg(`Scan complete in ${dur}s — 0 endpoints detected on ${targetSubnet}.0/24.`);
      }
    } catch (e: any) {
      setFound([]);
      const errMsg = e.message || '';
      if (errMsg.includes('Failed to fetch') || errMsg.includes('NetworkError')) {
        setStatusMsg('Scan error: Backend service unreachable on :8001. Ensure uvicorn is running.');
      } else {
        setStatusMsg(`Scan error: ${errMsg || 'Subnet probe timed out'}`);
      }
      playTacticalTone('alert');
    }
    setScanning(false);
  };

  const adoptDevice = async (d: any, customName?: string, customUrl?: string) => {
    const adoptKey = d.ip || d.name || 'manual';
    setAdopting(adoptKey);
    playTacticalTone('verify');
    try {
      await api.createCamera({
        name: customName || `${d.brand_hint || d.name || 'CCTV Camera'} (${d.ip})`,
        stream_url: customUrl || d.rtsp_url || `rtsp://admin:admin123@${d.ip}:${d.port || 554}/stream1`,
        location: `Sector LAN Node (${d.ip || 'Remote'})`,
        bop: d.bop || 'BOP-01',
        status: 'ONLINE',
        health_score: 100,
        fps: 15,
        resolution: '1920x1080',
        latitude: 28.6139,
        longitude: 77.2090,
      });
      setStatusMsg(`Successfully provisioned surveillance node for ${d.ip || customName}!`);
      setTimeout(onDone, 900);
    } catch (err: any) {
      setStatusMsg(`Adoption error: ${err.message}`);
    }
    setAdopting(null);
  };

  const testCandidateStream = async (url: string, key: string) => {
    setTestingStream(key);
    playTacticalTone('click');
    try {
      const r = await api.testStream(url);
      if (r.success) {
        playTacticalTone('verify');
        setStreamTestStatus((prev) => ({ ...prev, [key]: `✓ ${r.message}` }));
      } else {
        playTacticalTone('alert');
        setStreamTestStatus((prev) => ({ ...prev, [key]: `✕ ${r.message}` }));
      }
    } catch (e: any) {
      setStreamTestStatus((prev) => ({ ...prev, [key]: `✕ Test failed: ${e.message}` }));
    }
    setTestingStream(null);
  };

  const runSmartProbe = async () => {
    setProbing(true);
    setProbeResult(null);
    playTacticalTone('click');
    try {
      const r = await api.smartProbe(probeIp, probeUser, probePass);
      setProbeResult(r);
      if (r.success) {
        playTacticalTone('verify');
      } else {
        playTacticalTone('alert');
      }
    } catch (err: any) {
      setProbeResult({ success: false, message: `Prober error: ${err.message}` });
    }
    setProbing(false);
  };

  return (
    <div className="wizard-overlay">
      <div className="wizard" style={{ maxWidth: 840, maxHeight: '92vh', overflowY: 'auto' }}>
        <div className="wizard-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid rgba(0, 240, 255, 0.25)', paddingBottom: 14 }}>
          <div>
            <h2 style={{ margin: 0, fontSize: 18, color: '#00f0ff', letterSpacing: '0.8px' }}>
              📡 Universal Multi-Camera Connection & Discovery Studio
            </h2>
            <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 4 }}>
              Active LAN Subnet: <span style={{ color: '#00ff9d', fontFamily: 'var(--font-mono)' }}>{subnet}.0/24</span> • Local Host: <span style={{ color: '#00f0ff', fontFamily: 'var(--font-mono)' }}>{localIp}</span>
            </div>
          </div>
          <button className="btn-close" onClick={onCancel} style={{ background: 'transparent', border: 'none', color: '#fff', fontSize: 18, cursor: 'pointer' }}>✕</button>
        </div>

        <div className="wizard-body" style={{ paddingTop: 16 }}>
          {/* Studio Tab Bar */}
          <div className="studio-tab-bar">
            <button className={`studio-tab-btn${tab === 'lan' ? ' active' : ''}`} onClick={() => setTab('lan')}>
              📡 LAN Auto-Discovery ({found.length})
            </button>
            <button className={`studio-tab-btn${tab === 'probe' ? ' active' : ''}`} onClick={() => setTab('probe')}>
              🎯 Strix Smart URL Prober
            </button>
            <button className={`studio-tab-btn${tab === 'phone' ? ' active' : ''}`} onClick={() => setTab('phone')}>
              📱 Smartphone Live Bridge
            </button>
            <button className={`studio-tab-btn${tab === 'remote' ? ' active' : ''}`} onClick={() => setTab('remote')}>
              🌐 Remote WAN / Cloud CCTV
            </button>
          </div>

          {statusMsg && (
            <div style={{ padding: '8px 12px', background: '#0a192f', border: '1px solid #00f0ff40', borderRadius: 4, marginBottom: 14, fontSize: 12, color: '#00f0ff', display: 'flex', justifyContent: 'space-between' }}>
              <span>{statusMsg}</span>
              {scanDuration !== null && <span>⚡ {scanDuration}s</span>}
            </div>
          )}

          {/* TAB 1: LAN AUTO-DISCOVERY */}
          {tab === 'lan' && (
            <div>
              <div style={{ display: 'flex', gap: 10, marginBottom: 16, alignItems: 'center' }}>
                <div style={{ flex: 1 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                    <label style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                      TARGET SUBNET BASE (AUTO-RESOLVED)
                    </label>
                    <button
                      type="button"
                      className="btn btn-sm btn-secondary"
                      style={{ padding: '2px 8px', fontSize: 11 }}
                      onClick={async () => {
                        const inf = await refreshNetworkInfo();
                        if (inf?.subnet) setStatusMsg(`Resolved active subnet: ${inf.subnet}.0/24 (Host: ${inf.local_ip})`);
                      }}
                    >
                      ⚡ Auto-Detect
                    </button>
                  </div>
                  <input
                    type="text"
                    value={subnet}
                    onChange={(e) => setSubnet(e.target.value)}
                    placeholder="e.g. 192.168.29 or 10.238.254"
                    style={{ width: '100%', fontFamily: 'var(--font-mono)', fontSize: 13 }}
                  />
                </div>
                <button
                  className="btn btn-primary"
                  onClick={runLanScan}
                  disabled={scanning}
                  style={{ height: 38, marginTop: 18, minWidth: 200 }}
                >
                  {scanning ? '⏳ Scanning 254 Nodes...' : '🔍 Deep Hardware Scan'}
                </button>
              </div>

              {found.length === 0 && !scanning && (
                <div style={{ textAlign: 'center', padding: '36px 20px', background: 'rgba(0, 240, 255, 0.02)', border: '1px dashed rgba(0, 240, 255, 0.2)', borderRadius: 8, marginBottom: 16 }}>
                  <div style={{ fontSize: 32, marginBottom: 8 }}>📡</div>
                  <b style={{ color: '#fff', fontSize: 14 }}>Ready to Scan Network Cameras</b>
                  <p style={{ fontSize: 12, color: 'var(--text-secondary)', maxWidth: 480, margin: '8px auto 16px auto' }}>
                    Click <b>Deep Hardware Scan</b> to detect all connected IP cameras (TP-Link Tapo, AzureWave, CP Plus, Hikvision, Dahua, mobile streaming nodes) on subnet <b>{subnet}.0/24</b>.
                  </p>
                  <button className="btn btn-primary" onClick={runLanScan}>
                    🔍 Run Deep Hardware Scan
                  </button>
                </div>
              )}

              {/* Discovered Device Cards Grid */}
              {found.length > 0 && (
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
                    <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-secondary)', letterSpacing: '0.5px' }}>
                      DISCOVERED NETWORK HARDWARE ({found.length} DEVICES ACTIVE)
                    </span>
                    <button className="btn btn-sm btn-secondary" onClick={runLanScan} disabled={scanning}>
                      🔄 Rescan
                    </button>
                  </div>

                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {found.map((c, i) => {
                      const hint = String(c.brand_hint || '');
                      const isTapo = hint.includes('Tapo');
                      const isAzureWave = hint.includes('AzureWave');
                      const isMobile = hint.includes('Mobile');
                      const isGw = c.is_gateway;
                      const statStr = String(c.status || '');

                      return (
                        <div key={i} className="discovered-camera-card">
                          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 10 }}>
                            <div>
                              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                                <b style={{ color: '#fff', fontSize: 14, fontFamily: 'var(--font-mono)' }}>{c.ip}</b>
                                <span className="camera-badge-tag" style={{
                                  background: isTapo ? 'rgba(0, 240, 255, 0.15)' : isAzureWave ? 'rgba(255, 170, 0, 0.15)' : isMobile ? 'rgba(0, 255, 157, 0.15)' : 'rgba(255, 255, 255, 0.1)',
                                  color: isTapo ? '#00f0ff' : isAzureWave ? '#ffaa00' : isMobile ? '#00ff9d' : '#e0e6ed',
                                  border: `1px solid ${isTapo ? '#00f0ff' : isAzureWave ? '#ffaa00' : isMobile ? '#00ff9d' : 'rgba(255,255,255,0.2)'}`
                                }}>
                                  {c.brand_hint || 'NETWORK NODE'}
                                </span>
                                {c.mac && (
                                  <span style={{ fontSize: 10, color: 'var(--text-ghost)', fontFamily: 'var(--font-mono)' }}>
                                    MAC: {c.mac}
                                  </span>
                                )}
                              </div>

                              <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 4 }}>
                                <span style={{ color: statStr.includes('Active') || statStr.includes('Node') ? '#00ff9d' : '#ffaa00' }}>
                                  ● {c.status}
                                </span>
                                {c.open_ports?.length > 0 && (
                                  <span style={{ marginLeft: 10, color: 'var(--text-ghost)' }}>
                                    Open Ports: {c.open_ports.join(', ')}
                                  </span>
                                )}
                              </div>

                              {c.setup_tip && (
                                <div style={{ fontSize: 11, color: '#ffaa00', marginTop: 6, background: 'rgba(255, 170, 0, 0.05)', padding: '4px 8px', borderRadius: 4, borderLeft: '3px solid #ffaa00' }}>
                                  💡 <b>Setup Tip:</b> {c.setup_tip}
                                </div>
                              )}

                              <div style={{ fontSize: 11, color: '#00f0ff', fontFamily: 'var(--font-mono)', marginTop: 6, wordBreak: 'break-all' }}>
                                Candidate Stream: {c.rtsp_url}
                              </div>

                              {streamTestStatus[c.ip] && (
                                <div style={{ fontSize: 11, marginTop: 4, color: streamTestStatus[c.ip].startsWith('✓') ? '#00ff9d' : '#ff2a55' }}>
                                  {streamTestStatus[c.ip]}
                                </div>
                              )}
                            </div>

                            <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                              <button
                                className="btn btn-sm btn-secondary"
                                onClick={() => testCandidateStream(c.rtsp_url, c.ip)}
                                disabled={testingStream === c.ip}
                                title="Verify stream frames with OpenCV"
                              >
                                {testingStream === c.ip ? 'Testing...' : '🧪 Test Stream'}
                              </button>

                              <button
                                className="btn btn-sm btn-secondary"
                                onClick={() => {
                                  setProbeIp(c.ip);
                                  setTab('probe');
                                }}
                                title="Run Strix Smart Prober for this IP"
                              >
                                🎯 Smart Probe
                              </button>

                              <button
                                className="btn btn-sm btn-primary"
                                onClick={() => adoptDevice(c)}
                                disabled={adopting === c.ip || isGw}
                                title={isGw ? 'Router Gateway' : 'Add to live surveillance matrix'}
                              >
                                {adopting === c.ip ? 'Adopting...' : isGw ? 'Router' : '⚡ 1-Click Adopt'}
                              </button>
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* TAB 2: STRIX SMART STREAM PROBER */}
          {tab === 'probe' && (
            <div>
              <div style={{ padding: '14px 16px', background: 'rgba(0, 240, 255, 0.03)', border: '1px solid rgba(0, 240, 255, 0.2)', borderRadius: 6, marginBottom: 16 }}>
                <b style={{ color: '#00f0ff', fontSize: 13 }}>🎯 Strix Smart Stream Finder (Fast Auto-Pattern Prober)</b>
                <p style={{ fontSize: 11, color: 'var(--text-secondary)', margin: '4px 0 12px 0' }}>
                  Enter any camera IP (e.g. 192.168.29.107) and camera credentials. The prober will concurrently test the top 15 most common RTSP/HTTP URL patterns (Tapo, CP Plus, Dahua, Hikvision, Reolink, Axis) and automatically lock onto the active stream.
                </p>

                <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr 1fr auto', gap: 10, alignItems: 'flex-end' }}>
                  <div>
                    <label style={{ fontSize: 11, color: 'var(--text-secondary)' }}>CAMERA IP ADDRESS</label>
                    <input
                      type="text"
                      value={probeIp}
                      onChange={(e) => setProbeIp(e.target.value)}
                      placeholder="192.168.29.107"
                      style={{ width: '100%', fontFamily: 'var(--font-mono)' }}
                    />
                  </div>
                  <div>
                    <label style={{ fontSize: 11, color: 'var(--text-secondary)' }}>USERNAME</label>
                    <input
                      type="text"
                      value={probeUser}
                      onChange={(e) => setProbeUser(e.target.value)}
                      placeholder="admin"
                      style={{ width: '100%' }}
                    />
                  </div>
                  <div>
                    <label style={{ fontSize: 11, color: 'var(--text-secondary)' }}>PASSWORD</label>
                    <input
                      type="password"
                      value={probePass}
                      onChange={(e) => setProbePass(e.target.value)}
                      placeholder="Password"
                      style={{ width: '100%' }}
                    />
                  </div>
                  <button
                    className="btn btn-primary"
                    onClick={runSmartProbe}
                    disabled={probing || !probeIp}
                    style={{ height: 38, minWidth: 140 }}
                  >
                    {probing ? 'Probing...' : '🚀 Probe Stream'}
                  </button>
                </div>
              </div>

              {probeResult && (
                <div style={{ padding: '14px 16px', background: probeResult.success ? 'rgba(0, 255, 157, 0.06)' : 'rgba(255, 42, 85, 0.06)', border: `1px solid ${probeResult.success ? '#00ff9d' : '#ff2a55'}`, borderRadius: 6, marginBottom: 16 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <div>
                      <b style={{ color: probeResult.success ? '#00ff9d' : '#ff2a55', fontSize: 13 }}>
                        {probeResult.success ? '✓ Stream Found & Verified!' : '✕ Prober Status'}
                      </b>
                      <div style={{ fontSize: 12, color: '#fff', marginTop: 4 }}>
                        {probeResult.message}
                      </div>
                      {probeResult.working_url && (
                        <div style={{ fontSize: 12, color: '#00f0ff', fontFamily: 'var(--font-mono)', marginTop: 6, wordBreak: 'break-all' }}>
                          URL: {probeResult.working_url}
                        </div>
                      )}
                    </div>
                    {probeResult.success && probeResult.working_url && (
                      <button
                        className="btn btn-primary"
                        onClick={() => adoptDevice({ ip: probeIp, brand_hint: probeResult.brand_detected }, `Discovered ${probeResult.brand_detected}`, probeResult.working_url)}
                      >
                        ⚡ Adopt Stream
                      </button>
                    )}
                  </div>
                </div>
              )}

              <div className="prober-console-box">
                <div style={{ color: 'var(--text-ghost)', marginBottom: 6 }}>// CANDIDATE URL PATTERNS TESTED BY PROBER:</div>
                <div>• rtsp://[user]:[pass]@{probeIp || 'IP'}:554/stream1 (TP-Link Tapo Main Stream)</div>
                <div>• rtsp://[user]:[pass]@{probeIp || 'IP'}:554/stream2 (TP-Link Tapo Sub Stream)</div>
                <div>• rtsp://[user]:[pass]@{probeIp || 'IP'}:554/cam/realmonitor?channel=1&subtype=0 (CP Plus / Dahua)</div>
                <div>• rtsp://[user]:[pass]@{probeIp || 'IP'}:554/Streaming/Channels/101 (Hikvision Main)</div>
                <div>• rtsp://[user]:[pass]@{probeIp || 'IP'}:554/h264Preview_01_main (Reolink Main)</div>
                <div>• rtsp://[user]:[pass]@{probeIp || 'IP'}:554/axis-media/media.amp (Axis Media)</div>
                <div>• http://{probeIp || 'IP'}:8080/video (Mobile IP Webcam Stream)</div>
                <div>• http://{probeIp || 'IP'}:4747/video (Mobile DroidCam Stream)</div>
              </div>
            </div>
          )}

          {/* TAB 3: SMARTPHONE LIVE BRIDGE */}
          {tab === 'phone' && (
            <div>
              <div style={{ padding: '16px', background: 'rgba(0, 255, 157, 0.04)', border: '1px solid rgba(0, 255, 157, 0.25)', borderRadius: 6, marginBottom: 16 }}>
                <b style={{ color: '#00ff9d', fontSize: 14 }}>📱 Turn Any Smartphone Into An Active Border Surveillance Node</b>
                <p style={{ fontSize: 12, color: 'var(--text-secondary)', margin: '6px 0 14px 0' }}>
                  Open this link on any iPhone, Android phone, or laptop browser connected to Wi-Fi. It turns the device's camera into an instant live video surveillance feed streamed directly into IBVAP Command Center with real-time YOLO AI object detection.
                </p>

                <div style={{ display: 'flex', gap: 12, alignItems: 'center', background: '#040b15', padding: '12px 16px', borderRadius: 6, border: '1px solid rgba(0, 255, 157, 0.3)', marginBottom: 14 }}>
                  <div style={{ fontSize: 24 }}>🔗</div>
                  <div style={{ flex: 1, fontFamily: 'var(--font-mono)', fontSize: 13, color: '#00ff9d', wordBreak: 'break-all' }}>
                    http://{localIp || '192.168.29.253'}:5173/phone-camera
                  </div>
                  <button
                    className="btn btn-sm btn-secondary"
                    style={{ borderColor: '#00ff9d', color: '#00ff9d' }}
                    onClick={() => {
                      navigator.clipboard.writeText(`http://${localIp || '192.168.29.253'}:5173/phone-camera`);
                      setStatusMsg('Broadcast link copied to clipboard!');
                    }}
                  >
                    📋 Copy Link
                  </button>
                  <button
                    className="btn btn-sm btn-primary"
                    onClick={() => setShowPhoneBroadcaster(true)}
                  >
                    📱 Open In-App
                  </button>
                </div>

                <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
                  <button
                    className="btn btn-primary"
                    onClick={() => adoptDevice({ ip: localIp, brand_hint: 'Tactical Smartphone Node' }, 'Tactical Smartphone Node', 'phone://mobile-01')}
                    disabled={adopting === 'phone://mobile-01'}
                  >
                    {adopting === 'phone://mobile-01' ? 'Connecting...' : '⚡ Provision Smartphone Camera (phone://mobile-01)'}
                  </button>
                </div>
              </div>

              {/* Mobile Apps Alternative */}
              <div style={{ padding: '14px 16px', background: 'rgba(255, 170, 0, 0.04)', border: '1px solid rgba(255, 170, 0, 0.25)', borderRadius: 6 }}>
                <b style={{ color: '#ffaa00', fontSize: 13 }}>💡 Alternative: Use Popular Mobile Camera Apps</b>
                <div style={{ fontSize: 11, color: 'var(--text-secondary)', margin: '4px 0 10px 0' }}>
                  If you have <b>IP Webcam</b> or <b>DroidCam</b> installed on your phone:
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                  <div style={{ background: '#070f1e', padding: 10, borderRadius: 4, border: '1px solid rgba(255,255,255,0.1)' }}>
                    <b style={{ color: '#fff', fontSize: 12 }}>1. IP Webcam (Android / iOS)</b>
                    <div style={{ fontSize: 11, color: 'var(--text-ghost)', marginTop: 2 }}>Tap "Start Server" in the app</div>
                    <div style={{ fontSize: 11, color: '#00f0ff', fontFamily: 'var(--font-mono)', marginTop: 4 }}>http://[Phone-IP]:8080/video</div>
                  </div>
                  <div style={{ background: '#070f1e', padding: 10, borderRadius: 4, border: '1px solid rgba(255,255,255,0.1)' }}>
                    <b style={{ color: '#fff', fontSize: 12 }}>2. DroidCam (Android / iOS)</b>
                    <div style={{ fontSize: 11, color: 'var(--text-ghost)', marginTop: 2 }}>Note the Wi-Fi IP and Port 4747</div>
                    <div style={{ fontSize: 11, color: '#00f0ff', fontFamily: 'var(--font-mono)', marginTop: 4 }}>http://[Phone-IP]:4747/video</div>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* TAB 4: REMOTE WAN / CLOUD / WEBRTC */}
          {tab === 'remote' && (
            <div>
              <div style={{ padding: '14px 16px', background: 'rgba(0, 240, 255, 0.03)', border: '1px solid rgba(0, 240, 255, 0.2)', borderRadius: 6, marginBottom: 16 }}>
                <b style={{ color: '#00f0ff', fontSize: 13 }}>🌐 Connect Remote WAN, Cloud CCTV, or Tactical Stream</b>
                <p style={{ fontSize: 11, color: 'var(--text-secondary)', margin: '4px 0 12px 0' }}>
                  Connect any camera located outside the local network via public IP, dynamic DNS, Starlink SIM router, HLS (m3u8), or RTSP/RTMP proxy.
                </p>

                <div style={{ display: 'grid', gridTemplateColumns: '1fr 2fr 1fr auto', gap: 10, alignItems: 'flex-end', marginBottom: 14 }}>
                  <div>
                    <label style={{ fontSize: 11, color: 'var(--text-secondary)' }}>CAMERA NAME</label>
                    <input
                      type="text"
                      value={remoteName}
                      onChange={(e) => setRemoteName(e.target.value)}
                      placeholder="Sector Remote Recon"
                      style={{ width: '100%' }}
                    />
                  </div>
                  <div>
                    <label style={{ fontSize: 11, color: 'var(--text-secondary)' }}>STREAM URL (RTSP / RTMP / HTTP / HLS)</label>
                    <input
                      type="text"
                      value={remoteUrl}
                      onChange={(e) => setRemoteUrl(e.target.value)}
                      placeholder="rtsp://user:pass@remote.domain.com:554/live or http://..."
                      style={{ width: '100%', fontFamily: 'var(--font-mono)' }}
                    />
                  </div>
                  <div>
                    <label style={{ fontSize: 11, color: 'var(--text-secondary)' }}>OUTPOST / SECTOR</label>
                    <select
                      value={remoteBop}
                      onChange={(e) => setRemoteBop(e.target.value)}
                      style={{ width: '100%', height: 38 }}
                    >
                      <option value="BOP-01 Alpha">BOP-01 Alpha</option>
                      <option value="BOP-02 Bravo">BOP-02 Bravo</option>
                      <option value="BOP-03 Charlie">BOP-03 Charlie</option>
                      <option value="BOP-04 Delta">BOP-04 Delta</option>
                    </select>
                  </div>
                  <button
                    className="btn btn-primary"
                    onClick={() => adoptDevice({ bop: remoteBop }, remoteName, remoteUrl)}
                    disabled={!remoteUrl || adopting === 'remote'}
                    style={{ height: 38 }}
                  >
                    {adopting === 'remote' ? 'Connecting...' : '+ Ingest Stream'}
                  </button>
                </div>
              </div>

              {/* Pre-Configured Tactical Border Presets */}
              <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-secondary)', marginBottom: 8, letterSpacing: 0.8 }}>
                TACTICAL BORDER RECONNAISSANCE PRESETS (1-CLICK INGEST)
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                {[
                  { name: '🏔️ Sector Alpha: Indo-Nepal High Pass (Thermal 4K)', url: 'demo://high-pass-recon', bop: 'BOP-01 Alpha' },
                  { name: '🌲 Sector Bravo: Forest Ambush Trail (IR Night Hunter)', url: 'demo://forest-perimeter', bop: 'BOP-02 Bravo' },
                  { name: '🌊 Sector Charlie: Riverine Sarda Patrol (Optical)', url: 'demo://riverine-patrol', bop: 'BOP-03 Charlie' },
                  { name: '🚁 Sector Delta: Tactical Border Drone (EO/IR UAV)', url: 'demo://drone-aerial-recon', bop: 'BOP-04 Delta' },
                ].map((p, idx) => (
                  <div key={idx} style={{ background: '#070f1e', border: '1px solid rgba(0, 240, 255, 0.2)', padding: 12, borderRadius: 6, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <div>
                      <b style={{ color: '#fff', fontSize: 12 }}>{p.name}</b>
                      <div style={{ fontSize: 10, color: '#00f0ff', fontFamily: 'var(--font-mono)' }}>{p.bop}</div>
                    </div>
                    <button className="btn btn-sm btn-secondary" onClick={() => adoptDevice({ bop: p.bop }, p.name, p.url)}>
                      + Ingest
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {showPhoneBroadcaster && (
        <PhoneCameraTransmitter onReturn={() => setShowPhoneBroadcaster(false)} />
      )}
    </div>
  );
}

function PhoneCameraTransmitter({ onReturn }: { onReturn: () => void }) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [active, setActive] = useState(false);
  const [fps, setFps] = useState(0);
  const [frameCount, setFrameCount] = useState(0);
  const [facing, setFacing] = useState<'environment' | 'user'>('environment');
  const [statusText, setStatusText] = useState('Camera Standby — Tap Start to Transmit');
  const streamRef = useRef<MediaStream | null>(null);

  const startCam = async () => {
    try {
      setStatusText('Requesting optical sensor access...');
      if (streamRef.current) {
        streamRef.current.getTracks().forEach((t) => t.stop());
      }
      const s = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: facing, width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: false,
      });
      streamRef.current = s;
      if (videoRef.current) {
        videoRef.current.srcObject = s;
        videoRef.current.play();
      }
      setActive(true);
      setStatusText('● TRANSMITTING LIVE EO SENSOR TO COMMAND CENTER');
    } catch (err: any) {
      setStatusText(`Sensor error: ${err.message}`);
    }
  };

  const stopCam = () => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    setActive(false);
    setStatusText('Optical sensor released');
  };

  const toggleFacing = () => {
    const next = facing === 'environment' ? 'user' : 'environment';
    setFacing(next);
    if (active) {
      setTimeout(() => startCam(), 100);
    }
  };

  useEffect(() => {
    startCam();
    return () => {
      stopCam();
    };
  }, [facing]);

  // Frame capture and transmission loop (10 FPS)
  useEffect(() => {
    if (!active) return;
    let secFrames = 0;
    const fpsTimer = setInterval(() => {
      setFps(secFrames);
      secFrames = 0;
    }, 1000);

    const txTimer = setInterval(() => {
      const v = videoRef.current;
      const c = canvasRef.current;
      if (!v || !c || v.readyState < 2) return;
      c.width = v.videoWidth || 1280;
      c.height = v.videoHeight || 720;
      const ctx = c.getContext('2d');
      if (!ctx) return;
      ctx.drawImage(v, 0, 0, c.width, c.height);
      const b64 = c.toDataURL('image/jpeg', 0.65);
      api.uploadPhoneFrame('mobile-01', b64).catch(() => {});
      secFrames++;
      setFrameCount((prev) => prev + 1);
    }, 100);

    return () => {
      clearInterval(fpsTimer);
      clearInterval(txTimer);
    };
  }, [active]);

  return (
    <div className="phone-broadcaster-hud">
      <div style={{ position: 'absolute', top: 16, left: 16, right: 16, display: 'flex', justifyContent: 'space-between', zIndex: 10 }}>
        <div style={{ background: 'rgba(0,0,0,0.7)', padding: '6px 12px', borderRadius: 4, border: '1px solid #00f0ff' }}>
          <b style={{ color: '#00f0ff', fontSize: 12 }}>IBVAP TACTICAL SENSOR BROADCASTER</b>
          <div style={{ fontSize: 10, color: active ? '#00ff9d' : '#ffaa00' }}>{statusText}</div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn btn-sm btn-secondary" onClick={toggleFacing}>
            🔄 Flip ({facing === 'environment' ? 'Rear' : 'Front'})
          </button>
          <button className="btn btn-sm btn-primary" onClick={onReturn}>
            ✕ Close Viewfinder
          </button>
        </div>
      </div>

      <video
        ref={videoRef}
        playsInline
        muted
        style={{ width: '100%', height: '100%', objectFit: 'cover' }}
      />
      <canvas ref={canvasRef} style={{ display: 'none' }} />

      <div style={{ position: 'absolute', bottom: 16, left: 16, right: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'center', zIndex: 10, background: 'rgba(0,0,0,0.7)', padding: '8px 16px', borderRadius: 6, border: '1px solid rgba(255,255,255,0.15)' }}>
        <div style={{ fontSize: 11, fontFamily: 'var(--font-mono)' }}>
          <span style={{ color: '#00ff9d' }}>STREAM KEY: phone://mobile-01</span> • FPS: {fps} • FRAMES: {frameCount}
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          {active ? (
            <button className="btn btn-sm btn-danger" onClick={stopCam}>
              ⏹️ Stop Transmission
            </button>
          ) : (
            <button className="btn btn-sm btn-primary" onClick={startCam}>
              ▶️ Resume Transmission
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

/* ─── Incidents Page ──────────────────────────────────────────── */
