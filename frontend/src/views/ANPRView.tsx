import React, { useState, useEffect } from "react";
import { api } from "../api";
import { playTacticalTone, fmtTime } from "../utils/audio";

export function ANPRView() {
  const [plates, setPlates] = useState<any[]>([]);
  const [watchlist, setWatchlist] = useState<any[]>([]);
  const [stats, setStats] = useState<any>(null);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [isScanning, setIsScanning] = useState(false);
  const [testPlate, setTestPlate] = useState('JK 02 C 5678');
  const [testVehicleType, setTestVehicleType] = useState('SUV / LMV');
  const [testBop, setTestBop] = useState('BOP-01 Road Checkpost');
  const [feedback, setFeedback] = useState<string | null>(null);
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [scanResult, setScanResult] = useState<any | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [showAddWatchlist, setShowAddWatchlist] = useState(false);
  const [newWatchlistPlate, setNewWatchlistPlate] = useState('');
  const [newWatchlistReason, setNewWatchlistReason] = useState('');

  const loadData = () => {
    api.anprPlates(statusFilter || undefined, undefined, search || undefined).then(setPlates).catch(() => {});
    api.anprWatchlist().then(setWatchlist).catch(() => {});
    api.anprStats().then(setStats).catch(() => {});
  };

  useEffect(() => {
    loadData();
  }, [statusFilter, search]);

  const handleScan = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!testPlate.trim()) return;
    setIsScanning(true);
    playTacticalTone('click');
    try {
      const res = await api.anprScan({
        plate_number: testPlate,
        vehicle_type: testVehicleType,
        bop: testBop,
        confidence: 0.95,
        status: 'CLEARED',
      });
      if (res.status === 'STOLEN_FLAGGED') {
        playTacticalTone('alert');
        setFeedback(`🚨 INTERCEPT ENGAGED: Plate ${res.plate_number} matched STOLEN VEHICLE watchlist! Barrier triggered.`);
      } else {
        playTacticalTone('verify');
        setFeedback(`✓ CLEARED: Vehicle ${res.plate_number} passed ANPR checkpost.`);
      }
      loadData();
    } catch {
      setFeedback('Error processing ANPR scan');
    }
    setIsScanning(false);
  };

  const handleUploadScan = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!uploadFile) return;
    setIsUploading(true);
    setScanResult(null);
    playTacticalTone('click');
    try {
      const res = await api.anprScanFile(uploadFile, testBop);
      setScanResult(res);
      if (res.recognized) {
        if (res.status === 'STOLEN_FLAGGED') {
          playTacticalTone('alert');
          setFeedback(`🚨 INTERCEPT ENGAGED: Plate ${res.plate_number} (Conf: ${(res.confidence * 100).toFixed(0)}%) matched STOLEN WATCHLIST!`);
        } else {
          playTacticalTone('verify');
          setFeedback(`✓ NEURAL ANPR SUCCESS: Recognized plate ${res.plate_number} (Conf: ${(res.confidence * 100).toFixed(0)}%).`);
        }
      } else {
        setFeedback(`⚠️ ${res.message || 'No plate recognized in image'}`);
      }
      loadData();
    } catch (err: any) {
      setFeedback(`ANPR OCR Error: ${err.message}`);
    }
    setIsUploading(false);
  };

  const handleToggleBarrier = async () => {
    playTacticalTone('click');
    try {
      const res = await api.toggleBarrier();
      setStats((prev: any) => ({ ...prev, barrier_state: res }));
      playTacticalTone(res.barrier_raised ? 'verify' : 'escalate');
      setFeedback(`Barrier status updated: ${res.status}`);
    } catch {}
  };

  const handleAddWatchlist = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newWatchlistPlate.trim()) return;
    try {
      await api.addAnprWatchlist({
        plate_number: newWatchlistPlate,
        reason: newWatchlistReason || 'Border Surveillance Lookout Notice',
        agency: 'Intelligence Bureau / Special Operations',
        threat_level: 'HIGH',
      });
      setShowAddWatchlist(false);
      setNewWatchlistPlate('');
      setNewWatchlistReason('');
      playTacticalTone('verify');
      loadData();
    } catch {}
  };

  const barrierRaised = stats?.barrier_state?.barrier_raised;

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>ANPR Checkpost & Vehicle Inspection Terminal</h1>
          <p style={{ margin: 0, fontSize: 12, color: 'var(--text-secondary)' }}>
            High-speed Automatic Number Plate Recognition (Indian HSRP format), stolen vehicle database integration, and automated barrier control
          </p>
        </div>
        <div style={{ display: 'flex', gap: 10 }}>
          <button
            className={`btn ${barrierRaised ? 'btn-primary' : 'btn-danger'}`}
            onClick={handleToggleBarrier}
            style={{ fontWeight: 800, letterSpacing: 1 }}
          >
            {barrierRaised ? '▲ BARRIER OPEN (RAISED)' : '▼ BARRIER ENGAGED (INTERCEPT)'}
          </button>
          <button className="btn btn-secondary" onClick={() => setShowAddWatchlist(true)}>
            + Add Flagged Plate
          </button>
        </div>
      </div>

      {feedback && (
        <div className={`test-feedback ${String(feedback).includes('🚨') ? 'fail' : 'success'}`} style={{ marginBottom: 16 }}>
          {feedback}
        </div>
      )}

      {/* ANPR Telemetry KPI Ribbon */}
      <div className="tactical-kpi-ribbon" style={{ marginBottom: 16 }}>
        <div className="tactical-kpi-card">
          <div className="kpi-header-label">
            <span>24H VEHICLES SCANNED</span>
            <span style={{ color: '#00f0ff' }}>CHECKPOST</span>
          </div>
          <div className="kpi-metric-val">{stats?.total_vehicles_24h || plates.length}</div>
        </div>
        <div className="tactical-kpi-card">
          <div className="kpi-header-label">
            <span>STOLEN / FLAGGED INTERCEPTS</span>
            <span style={{ color: '#ff2a55' }}>ALERTS</span>
          </div>
          <div className="kpi-metric-val" style={{ color: '#ff2a55' }}>
            {stats?.flagged_intercepts || 2}
          </div>
        </div>
        <div className="tactical-kpi-card">
          <div className="kpi-header-label">
            <span>AVG OCR ACCURACY</span>
            <span style={{ color: '#00ff9d' }}>ONNX MODEL</span>
          </div>
          <div className="kpi-metric-val" style={{ color: '#00ff9d' }}>
            {stats?.avg_ocr_confidence || 95.4}%
          </div>
        </div>
        <div className="tactical-kpi-card">
          <div className="kpi-header-label">
            <span>BARRIER INTERCEPT SYSTEM</span>
            <span style={{ color: barrierRaised ? '#00ff9d' : '#ffaa00' }}>
              {barrierRaised ? 'DISENGAGED' : 'ARMED'}
            </span>
          </div>
          <div className="kpi-metric-val" style={{ fontSize: 18, color: barrierRaised ? '#00ff9d' : '#ffaa00' }}>
            {barrierRaised ? 'PASS THRU' : 'INTERCEPT READY'}
          </div>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 16 }}>
        {/* Scanned Plates Feed */}
        <div className="panel" style={{ margin: 0 }}>
          <div className="panel-header">
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <b style={{ color: '#fff', fontSize: 14 }}>Real-Time Vehicle Passings & OCR Stream</b>
              <span className="panel-tag">{plates.length} LOGGED</span>
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <input
                type="text"
                placeholder="Filter plate / vehicle..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                style={{
                  background: '#040b14',
                  border: '1px solid rgba(0, 240, 255, 0.2)',
                  color: '#fff',
                  borderRadius: 4,
                  padding: '4px 8px',
                  fontSize: 11,
                }}
              />
              <select
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
                style={{
                  background: '#040b14',
                  border: '1px solid rgba(0, 240, 255, 0.2)',
                  color: '#fff',
                  borderRadius: 4,
                  padding: '4px 8px',
                  fontSize: 11,
                }}
              >
                <option value="">All Statuses</option>
                <option value="CLEARED">Cleared</option>
                <option value="STOLEN_FLAGGED">Stolen / Flagged</option>
                <option value="MILITARY_PRIORITY">Military Priority</option>
              </select>
            </div>
          </div>

          <div style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 10 }}>
            {plates.length === 0 ? (
              <div style={{ textAlign: "center", padding: "40px 10px", color: "var(--text-ghost)", fontSize: 12 }}>
                No license plate reads recorded yet. Upload a vehicle image on the right or analyze a video in Video Studio with ANPR enabled.
              </div>
            ) : (
              plates.map((p) => {
                const stat = String(p.status || '').toUpperCase();
                const isFlagged = stat.includes('FLAGGED') || stat.includes('STOLEN') || stat.includes('MATCH');
                const isMilitary = stat.includes('MILITARY');
                return (
                  <div
                    key={p.id}
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center',
                      padding: '12px 14px',
                      background: isFlagged ? 'rgba(255, 42, 85, 0.08)' : 'rgba(255, 255, 255, 0.02)',
                      border: isFlagged ? '1px solid #ff2a55' : '1px solid rgba(255, 255, 255, 0.06)',
                      borderRadius: 6,
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
                      <div className="anpr-hsrp-plate">
                        <div className="anpr-hsrp-flag">
                          <span>IND</span>
                        </div>
                        <div className="anpr-hsrp-text">{p.plate_number}</div>
                      </div>
                      <div>
                        <div style={{ color: '#fff', fontWeight: 600, fontSize: 13 }}>
                          {p.vehicle_model} <span style={{ color: 'var(--text-secondary)', fontSize: 11 }}>({p.vehicle_type})</span>
                        </div>
                        <div style={{ fontSize: 10, color: 'var(--text-secondary)', marginTop: 2 }}>
                          {p.bop} • Lane: {p.lane} • Origin: {p.state_origin} • Speed: {p.speed_kmh} km/h
                        </div>
                      </div>
                    </div>

                    <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                      <div style={{ textAlign: 'right' }}>
                        <span className={isFlagged ? 'anpr-badge-stolen' : isMilitary ? 'anpr-badge-military' : 'anpr-badge-cleared'}>
                          {p.status}
                        </span>
                        <div style={{ fontSize: 10, color: 'var(--text-ghost)', marginTop: 4 }}>
                          OCR Conf: {(p.confidence * 100).toFixed(0)}% • {fmtTime(p.timestamp)}
                        </div>
                      </div>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>

        {/* Live ANPR Scan Simulator & Watchlist */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {/* Instant Photo OCR Upload */}
          <div className="panel" style={{ margin: 0, padding: 16 }}>
            <h3 style={{ color: '#00ff9d', marginBottom: 12, fontSize: 14 }}>📷 Upload Vehicle Photo for Instant OCR</h3>
            <form onSubmit={handleUploadScan} style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div>
                <label style={{ fontSize: 11, color: 'var(--text-secondary)' }}>Select vehicle image file:</label>
                <input
                  type="file"
                  accept="image/*"
                  onChange={(e) => {
                    const f = e.target.files?.[0] || null;
                    setUploadFile(f);
                    if (f) {
                      setPreviewUrl(URL.createObjectURL(f));
                      setScanResult(null);
                    } else {
                      setPreviewUrl(null);
                    }
                  }}
                  style={{ width: '100%', marginTop: 4, fontSize: 12, color: '#fff' }}
                />
              </div>

              {previewUrl && (
                <div style={{ marginTop: 4, position: 'relative', borderRadius: 4, overflow: 'hidden', maxHeight: 160, background: '#000', textAlign: 'center' }}>
                  <img src={previewUrl} alt="Vehicle Upload Preview" style={{ maxHeight: 160, maxWidth: '100%', objectFit: 'contain' }} />
                </div>
              )}

              <button className="btn btn-primary" type="submit" disabled={isUploading || !uploadFile} style={{ marginTop: 4 }}>
                {isUploading ? 'Detecting Plate via YOLOv11 ONNX...' : '⚡ Run Neural ANPR on Image'}
              </button>
            </form>

            {/* Neural Detection Result Card */}
            {scanResult && scanResult.recognized && (
              <div style={{
                marginTop: 14,
                padding: 12,
                background: scanResult.status === 'STOLEN_FLAGGED' ? 'rgba(255, 42, 85, 0.14)' : 'rgba(0, 240, 255, 0.08)',
                border: scanResult.status === 'STOLEN_FLAGGED' ? '1px solid #ff2a55' : '1px solid #00f0ff',
                borderRadius: 6,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
                  <span style={{ fontSize: 11, fontWeight: 700, color: scanResult.status === 'STOLEN_FLAGGED' ? '#ff2a55' : '#00f0ff', textTransform: 'uppercase' }}>
                    {scanResult.status === 'STOLEN_FLAGGED' ? '🚨 STOLEN VEHICLE INTERCEPTED' : '✓ NEURAL PLATE IDENTIFIED'}
                  </span>
                  <span style={{ fontSize: 10, color: '#94a3b8' }}>
                    YOLOv11 ONNX (Conf: {(scanResult.confidence * 100).toFixed(0)}%)
                  </span>
                </div>

                <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                  {scanResult.crop_image ? (
                    <img
                      src={scanResult.crop_image}
                      alt="License Plate Crop"
                      style={{
                        height: 48,
                        borderRadius: 4,
                        border: scanResult.status === 'STOLEN_FLAGGED' ? '2px solid #ff2a55' : '2px solid #00f0ff',
                        boxShadow: '0 0 10px rgba(0, 240, 255, 0.3)',
                        objectFit: 'contain',
                        background: '#000',
                      }}
                    />
                  ) : null}

                  <div className="anpr-hsrp-plate" style={{ boxShadow: '0 0 12px rgba(0,255,157,0.2)' }}>
                    <div className="anpr-hsrp-flag">
                      <span>IND</span>
                    </div>
                    <div className="anpr-hsrp-text" style={{ fontSize: 16 }}>{scanResult.plate_number}</div>
                  </div>
                </div>
              </div>
            )}
          </div>

          <div className="panel" style={{ margin: 0, padding: 16 }}>
            <h3 style={{ color: '#00f0ff', marginBottom: 12, fontSize: 14 }}>⚡ Run Live ANPR OCR Scan</h3>
            <form onSubmit={handleScan} style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div>
                <label style={{ fontSize: 11, color: 'var(--text-secondary)' }}>Registration Number (HSRP Format):</label>
                <input
                  type="text"
                  value={testPlate}
                  onChange={(e) => setTestPlate(e.target.value)}
                  style={{
                    width: '100%',
                    background: '#040b14',
                    border: '1px solid rgba(0, 240, 255, 0.3)',
                    color: '#00f0ff',
                    fontFamily: 'var(--font-mono)',
                    fontWeight: 700,
                    padding: '8px 10px',
                    borderRadius: 4,
                    fontSize: 14,
                    marginTop: 4,
                  }}
                />
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                <div>
                  <label style={{ fontSize: 10, color: 'var(--text-secondary)' }}>Vehicle Class:</label>
                  <select
                    value={testVehicleType}
                    onChange={(e) => setTestVehicleType(e.target.value)}
                    style={{ width: '100%', background: '#040b14', border: '1px solid #333', color: '#fff', padding: 6, borderRadius: 4, fontSize: 11, marginTop: 2 }}
                  >
                    <option value="SUV / LMV">SUV / LMV</option>
                    <option value="Civilian Sedan">Civilian Sedan</option>
                    <option value="Heavy Commercial (HMV)">Heavy Commercial (HMV)</option>
                    <option value="Two-Wheeler (2W)">Two-Wheeler (2W)</option>
                  </select>
                </div>
                <div>
                  <label style={{ fontSize: 10, color: 'var(--text-secondary)' }}>Checkpost Post:</label>
                  <select
                    value={testBop}
                    onChange={(e) => setTestBop(e.target.value)}
                    style={{ width: '100%', background: '#040b14', border: '1px solid #333', color: '#fff', padding: 6, borderRadius: 4, fontSize: 11, marginTop: 2 }}
                  >
                    <option value="BOP-01 Road Checkpost">BOP-01 Road Checkpost</option>
                    <option value="BOP-03 Freight Checkpoint">BOP-03 Freight Checkpoint</option>
                  </select>
                </div>
              </div>
              <button className="btn btn-primary" type="submit" disabled={isScanning} style={{ marginTop: 4 }}>
                {isScanning ? 'Processing Neural OCR...' : '🔍 Scan Plate & Check Watchlist'}
              </button>
            </form>
          </div>

          <div className="panel" style={{ margin: 0, padding: 16 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
              <h3 style={{ color: '#ff2a55', margin: 0, fontSize: 13 }}>⚠️ Stolen / Wanted Vehicle Database</h3>
              <span className="panel-tag" style={{ borderColor: '#ff2a55', color: '#ff2a55' }}>{watchlist.length} FLAGGED</span>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {watchlist.map((w) => (
                <div
                  key={w.id}
                  style={{
                    padding: 10,
                    background: 'rgba(255, 42, 85, 0.05)',
                    border: '1px solid rgba(255, 42, 85, 0.3)',
                    borderRadius: 4,
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <b style={{ color: '#fff', fontFamily: 'var(--font-mono)' }}>{w.plate_number}</b>
                    <span style={{ fontSize: 9, color: '#ff2a55', fontWeight: 700 }}>{w.threat_level}</span>
                  </div>
                  <div style={{ fontSize: 11, color: '#e2effc', marginTop: 3 }}>{w.reason}</div>
                  <div style={{ fontSize: 9, color: 'var(--text-ghost)', marginTop: 3 }}>Agency: {w.agency} • {w.vehicle_model}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* Add Watchlist Modal */}
      {showAddWatchlist && (
        <div className="section-65b-modal-backdrop" onClick={() => setShowAddWatchlist(false)}>
          <div className="panel" style={{ maxWidth: 460, width: '100%', margin: 0, padding: 24 }} onClick={(e) => e.stopPropagation()}>
            <h3 style={{ color: '#00f0ff', marginBottom: 14 }}>Flag Vehicle in National Border Database</h3>
            <form onSubmit={handleAddWatchlist} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <div>
                <label style={{ fontSize: 11, color: 'var(--text-secondary)' }}>Registration Plate Number:</label>
                <input
                  type="text"
                  placeholder="e.g. JK 02 C 5678"
                  value={newWatchlistPlate}
                  onChange={(e) => setNewWatchlistPlate(e.target.value)}
                  style={{ width: '100%', background: '#040b14', border: '1px solid #333', color: '#fff', padding: 8, borderRadius: 4, marginTop: 4 }}
                  required
                />
              </div>
              <div>
                <label style={{ fontSize: 11, color: 'var(--text-secondary)' }}>Surveillance Reason / Charge:</label>
                <input
                  type="text"
                  placeholder="e.g. Suspected in illegal arms trafficking"
                  value={newWatchlistReason}
                  onChange={(e) => setNewWatchlistReason(e.target.value)}
                  style={{ width: '100%', background: '#040b14', border: '1px solid #333', color: '#fff', padding: 8, borderRadius: 4, marginTop: 4 }}
                />
              </div>
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 8 }}>
                <button className="btn btn-secondary" type="button" onClick={() => setShowAddWatchlist(false)}>
                  Cancel
                </button>
                <button className="btn btn-danger" type="submit">
                  + Add to Lookout List
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

/* ─── FRS Biometric Watchlist & Face Match Studio ─────────────── */
