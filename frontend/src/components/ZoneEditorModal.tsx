import React, { useState, useEffect, useRef } from 'react';
import { Camera, Zone } from '../types';
import { api } from '../api';
import { WebSocketVideoCanvas } from './WebSocketVideoCanvas';

interface ZoneEditorModalProps {
  cameras: Camera[];
  initialCameraId?: number;
  onClose: () => void;
  onSaved?: () => void;
}

const ZONE_PRESETS = [
  {
    name: 'Zero-Line Infiltration Corridor (Top 30%)',
    type: 'RESTRICTED',
    color: '#ff2a55',
    severity: 0.95,
    dwell: 10,
    polygon: [[0.05, 0.05], [0.95, 0.05], [0.95, 0.35], [0.05, 0.35]],
  },
  {
    name: 'Buffer DMZ Demarcation (Middle 50%)',
    type: 'SENSITIVE',
    color: '#ffaa00',
    severity: 0.70,
    dwell: 20,
    polygon: [[0.10, 0.35], [0.90, 0.35], [0.85, 0.75], [0.15, 0.75]],
  },
  {
    name: 'Checkpost Ingress Gate (Lower Center)',
    type: 'PATROL',
    color: '#00f0ff',
    severity: 0.50,
    dwell: 30,
    polygon: [[0.30, 0.65], [0.70, 0.65], [0.75, 0.95], [0.25, 0.95]],
  },
  {
    name: 'Directional Perimeter Tripwire (A -> B)',
    type: 'TRIPWIRE',
    color: '#a855f7',
    severity: 0.90,
    dwell: 5,
    polygon: [[0.05, 0.50], [0.95, 0.50], [0.95, 0.55], [0.05, 0.55]],
  },
];

export function ZoneEditorModal({ cameras, initialCameraId, onClose, onSaved }: ZoneEditorModalProps) {
  const [selectedCamId, setSelectedCamId] = useState<number>(
    initialCameraId || cameras[0]?.id || 1
  );
  const [existingZones, setExistingZones] = useState<Zone[]>([]);
  const [activePoints, setActivePoints] = useState<[number, number][]>([]);
  const [zoneName, setZoneName] = useState('Perimeter Zone Alpha');
  const [zoneType, setZoneType] = useState('RESTRICTED');
  const [zoneColor, setZoneColor] = useState('#ff2a55');
  const [severity, setSeverity] = useState(0.85);
  const [dwellThreshold, setDwellThreshold] = useState(15);
  const [isSaving, setIsSaving] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);

  const containerRef = useRef<HTMLDivElement>(null);
  const activeCamera = cameras.find((c) => c.id === selectedCamId) || cameras[0];

  // Load existing zones for selected camera
  const loadZones = async () => {
    try {
      const list = await api.zones(selectedCamId);
      setExistingZones(list || []);
    } catch {
      setExistingZones([]);
    }
  };

  useEffect(() => {
    loadZones();
    setActivePoints([]);
  }, [selectedCamId]);

  // Handle clicking on the canvas overlay to place a normalized vertex [0..1, 0..1]
  const handleCanvasClick = (e: React.MouseEvent<SVGSVGElement>) => {
    if (!containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    const rawX = e.clientX - rect.left;
    const rawY = e.clientY - rect.top;

    const normX = Math.max(0, Math.min(1, Math.round((rawX / rect.width) * 1000) / 1000));
    const normY = Math.max(0, Math.min(1, Math.round((rawY / rect.height) * 1000) / 1000));

    // If clicking close to the first point and >= 3 points, do nothing (user can save)
    if (activePoints.length >= 3) {
      const [firstX, firstY] = activePoints[0];
      const dist = Math.hypot(normX - firstX, normY - firstY);
      if (dist < 0.04) {
        setFeedback('Polygon closed! Adjust parameters and click Save Zone.');
        return;
      }
    }

    setActivePoints((prev) => [...prev, [normX, normY]]);
    setFeedback(null);
  };

  const handleApplyPreset = (preset: typeof ZONE_PRESETS[0]) => {
    setZoneName(`${activeCamera?.bop || 'Perimeter'} • ${preset.name}`);
    setZoneType(preset.type);
    setZoneColor(preset.color);
    setSeverity(preset.severity);
    setDwellThreshold(preset.dwell);
    setActivePoints(preset.polygon as [number, number][]);
    setFeedback(`Applied Preset: ${preset.name}`);
  };

  const handleUndo = () => {
    setActivePoints((prev) => prev.slice(0, -1));
  };

  const handleClear = () => {
    setActivePoints([]);
    setFeedback(null);
  };

  const handleSave = async () => {
    if (activePoints.length < 3) {
      setFeedback('⚠️ Place at least 3 vertices on the camera viewport to define a valid polygon zone.');
      return;
    }
    if (!zoneName.trim()) {
      setFeedback('⚠️ Please specify a name for this tactical zone.');
      return;
    }

    setIsSaving(true);
    try {
      await api.createZone({
        camera_id: selectedCamId,
        name: zoneName,
        zone_type: zoneType,
        polygon: activePoints,
        active: true,
        severity: severity,
        dwell_threshold_seconds: dwellThreshold,
        color: zoneColor,
        description: `Tactical ${zoneType} boundary on ${activeCamera?.name || 'Camera'}. Dwell: ${dwellThreshold}s.`,
      });

      setFeedback('✓ Perimeter zone saved & synced to Edge AI inference pipeline!');
      setActivePoints([]);
      loadZones();
      if (onSaved) onSaved();
    } catch (err: any) {
      setFeedback(`Error saving zone: ${err.message || 'Network error'}`);
    }
    setIsSaving(false);
  };

  const handleDeleteZone = async (zoneId: number) => {
    if (!confirm('Remove this virtual perimeter zone? Alerts for this region will cease immediately.')) return;
    try {
      await api.deleteZone(zoneId);
      loadZones();
      if (onSaved) onSaved();
    } catch {}
  };

  return (
    <div
      className="modal-backdrop"
      onClick={onClose}
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(2, 6, 12, 0.90)',
        backdropFilter: 'blur(10px)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 9999,
        padding: 24,
      }}
    >
      <div
        className="modal-content"
        onClick={(e) => e.stopPropagation()}
        style={{
          background: '#070c18',
          border: '1px solid rgba(0, 240, 255, 0.4)',
          borderRadius: 8,
          maxWidth: 1200,
          width: '100%',
          maxHeight: '92vh',
          display: 'flex',
          flexDirection: 'column',
          boxShadow: '0 0 60px rgba(0, 240, 255, 0.25)',
          overflow: 'hidden',
        }}
      >
        {/* Header */}
        <div
          style={{
            padding: '16px 20px',
            borderBottom: '1px solid rgba(0, 240, 255, 0.2)',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            background: 'rgba(0, 240, 255, 0.03)',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <span style={{ fontSize: 20 }}>📐</span>
            <div>
              <div style={{ fontSize: 10, color: '#00f0ff', letterSpacing: 2, fontFamily: 'var(--font-mono)' }}>
                DEFENSE ROI & VIRTUAL FENCE STUDIO
              </div>
              <h2 style={{ margin: 0, fontSize: 18, color: '#fff', letterSpacing: 0.5 }}>
                Perimeter Intrusion Zone & Tripwire Calibration
              </h2>
            </div>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            {/* Camera Switcher Dropdown */}
            <select
              value={selectedCamId}
              onChange={(e) => setSelectedCamId(Number(e.target.value))}
              style={{
                background: '#040914',
                color: '#00f0ff',
                border: '1px solid rgba(0, 240, 255, 0.4)',
                padding: '6px 12px',
                borderRadius: 4,
                fontFamily: 'var(--font-mono)',
                fontSize: 12,
                cursor: 'pointer',
              }}
            >
              {cameras.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name} ({c.bop})
                </option>
              ))}
            </select>

            <button className="btn btn-secondary btn-sm" onClick={onClose}>
              ✕ Close
            </button>
          </div>
        </div>

        {/* Body Layout: 2 Columns (Live Canvas Viewport + Zone Parameters Panel) */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 380px', flex: 1, minHeight: 0, overflow: 'hidden' }}>
          {/* Column 1: Live Interactive Drawing Viewport */}
          <div
            style={{
              padding: 20,
              display: 'flex',
              flexDirection: 'column',
              background: '#030710',
              borderRight: '1px solid rgba(255, 255, 255, 0.06)',
              overflowY: 'auto',
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
              <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                Target: <b style={{ color: '#fff' }}>{activeCamera?.name}</b> •{' '}
                <span style={{ color: '#00ff9d' }}>{activeCamera?.status}</span> • Resolution: {activeCamera?.resolution || '1280x720'}
              </div>
              <div style={{ fontSize: 11, color: '#00f0ff', fontFamily: 'var(--font-mono)' }}>
                {activePoints.length === 0
                  ? 'Click video viewport to start drawing vertices'
                  : `${activePoints.length} vertices plotted (click 1st point or Save)`}
              </div>
            </div>

            {/* Video Viewport Container with SVG Drawing Overlay */}
            <div
              ref={containerRef}
              style={{
                position: 'relative',
                width: '100%',
                aspectRatio: '16/9',
                background: '#010408',
                borderRadius: 6,
                overflow: 'hidden',
                border: '1px solid rgba(0, 240, 255, 0.3)',
                boxShadow: 'inset 0 0 30px rgba(0, 0, 0, 0.8)',
                cursor: 'crosshair',
              }}
            >
              {/* Underlying Live Stream */}
              <WebSocketVideoCanvas
                cameraId={selectedCamId}
                cameraName={activeCamera?.name}
                bop={activeCamera?.bop}
                fps={activeCamera?.fps}
                resolution={activeCamera?.resolution}
                status={activeCamera?.status}
                isMuted={true}
              />

              {/* Grid Lines Overlay */}
              <div
                style={{
                  position: 'absolute',
                  inset: 0,
                  pointerEvents: 'none',
                  backgroundImage: 'radial-gradient(rgba(0, 240, 255, 0.12) 1px, transparent 0)',
                  backgroundSize: '24px 24px',
                }}
              />

              {/* SVG Overlay for Polygons & Interactive Drawing */}
              <svg
                onClick={handleCanvasClick}
                style={{
                  position: 'absolute',
                  inset: 0,
                  width: '100%',
                  height: '100%',
                  zIndex: 20,
                }}
                viewBox="0 0 1000 1000"
                preserveAspectRatio="none"
              >
                {/* 1. Render Existing Saved Zones */}
                {existingZones.map((z) => {
                  if (!z.polygon || z.polygon.length < 3) return null;
                  const pts = z.polygon
                    .map(([x, y]) => `${x * 1000},${y * 1000}`)
                    .join(' ');
                  const col = z.color || '#00f0ff';
                  return (
                    <g key={z.id}>
                      <polygon
                        points={pts}
                        fill={col}
                        fillOpacity="0.15"
                        stroke={col}
                        strokeWidth="2"
                        strokeDasharray="6,4"
                      />
                      <text
                        x={z.polygon[0][0] * 1000 + 10}
                        y={z.polygon[0][1] * 1000 + 20}
                        fill={col}
                        fontSize="18"
                        fontFamily="monospace"
                        fontWeight="bold"
                      >
                        {z.name} ({z.zone_type})
                      </text>
                    </g>
                  );
                })}

                {/* 2. Render Active Polygon currently being drawn */}
                {activePoints.length >= 2 && (
                  <polyline
                    points={activePoints
                      .map(([x, y]) => `${x * 1000},${y * 1000}`)
                      .join(' ')}
                    fill="none"
                    stroke={zoneColor}
                    strokeWidth="3"
                  />
                )}

                {activePoints.length >= 3 && (
                  <polygon
                    points={activePoints
                      .map(([x, y]) => `${x * 1000},${y * 1000}`)
                      .join(' ')}
                    fill={zoneColor}
                    fillOpacity="0.25"
                    stroke={zoneColor}
                    strokeWidth="3"
                  />
                )}

                {/* 3. Render Vertices Handles */}
                {activePoints.map(([x, y], idx) => (
                  <g key={idx}>
                    <circle
                      cx={x * 1000}
                      cy={y * 1000}
                      r={idx === 0 ? 9 : 6}
                      fill={idx === 0 ? '#00ff9d' : zoneColor}
                      stroke="#fff"
                      strokeWidth="2"
                    />
                    <text
                      x={x * 1000 + 12}
                      y={y * 1000 + 4}
                      fill="#fff"
                      fontSize="14"
                      fontFamily="monospace"
                      fontWeight="bold"
                    >
                      P{idx + 1}
                    </text>
                  </g>
                ))}
              </svg>
            </div>

            {/* Drawing Tools Ribbon */}
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                marginTop: 12,
                padding: '10px 14px',
                background: 'rgba(255, 255, 255, 0.02)',
                border: '1px solid rgba(255, 255, 255, 0.08)',
                borderRadius: 4,
              }}
            >
              <div style={{ display: 'flex', gap: 8 }}>
                <button
                  className="btn btn-sm btn-secondary"
                  onClick={handleUndo}
                  disabled={activePoints.length === 0}
                  title="Remove last vertex"
                >
                  ↩ Undo Point
                </button>
                <button
                  className="btn btn-sm btn-secondary"
                  onClick={handleClear}
                  disabled={activePoints.length === 0}
                  title="Clear all plotted points"
                >
                  ✕ Clear Shape
                </button>
              </div>

              {/* Fast Presets */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ fontSize: 11, color: 'var(--text-ghost)' }}>Presets:</span>
                {ZONE_PRESETS.map((p, idx) => (
                  <button
                    key={idx}
                    className="btn btn-sm"
                    style={{
                      fontSize: 10,
                      padding: '4px 8px',
                      background: 'rgba(0, 240, 255, 0.08)',
                      borderColor: p.color,
                      color: p.color,
                    }}
                    onClick={() => handleApplyPreset(p)}
                  >
                    {p.type}
                  </button>
                ))}
              </div>
            </div>

            {feedback && (
              <div
                style={{
                  marginTop: 10,
                  padding: '8px 12px',
                  borderRadius: 4,
                  fontSize: 12,
                  background: String(feedback).includes('✓') ? 'rgba(0, 255, 157, 0.1)' : 'rgba(255, 42, 85, 0.1)',
                  border: String(feedback).includes('✓') ? '1px solid #00ff9d' : '1px solid #ff2a55',
                  color: String(feedback).includes('✓') ? '#00ff9d' : '#ff7a8a',
                }}
              >
                {feedback}
              </div>
            )}
          </div>

          {/* Column 2: Parameters & Active Zones List */}
          <div
            style={{
              padding: 20,
              background: '#050914',
              display: 'flex',
              flexDirection: 'column',
              gap: 16,
              overflowY: 'auto',
            }}
          >
            {/* Zone Properties Form */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <div style={{ fontSize: 12, fontWeight: 700, color: '#00f0ff', letterSpacing: 1 }}>
                ZONE PARAMETERS
              </div>

              <div>
                <label style={{ fontSize: 11, color: 'var(--text-secondary)' }}>Zone Designation Name:</label>
                <input
                  type="text"
                  value={zoneName}
                  onChange={(e) => setZoneName(e.target.value)}
                  style={{
                    width: '100%',
                    background: '#040b14',
                    border: '1px solid #333',
                    color: '#fff',
                    padding: '7px 10px',
                    borderRadius: 4,
                    fontSize: 12,
                    marginTop: 3,
                  }}
                />
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                <div>
                  <label style={{ fontSize: 11, color: 'var(--text-secondary)' }}>Zone Type:</label>
                  <select
                    value={zoneType}
                    onChange={(e) => {
                      setZoneType(e.target.value);
                      if (e.target.value === 'RESTRICTED') setZoneColor('#ff2a55');
                      else if (e.target.value === 'SENSITIVE') setZoneColor('#ffaa00');
                      else if (e.target.value === 'PATROL') setZoneColor('#00f0ff');
                      else if (e.target.value === 'TRIPWIRE') setZoneColor('#a855f7');
                    }}
                    style={{
                      width: '100%',
                      background: '#040b14',
                      border: '1px solid #333',
                      color: '#fff',
                      padding: '7px 8px',
                      borderRadius: 4,
                      fontSize: 11,
                      marginTop: 3,
                    }}
                  >
                    <option value="RESTRICTED">RESTRICTED (Zero-Line)</option>
                    <option value="SENSITIVE">SENSITIVE (Buffer DMZ)</option>
                    <option value="PATROL">PATROL (Checkpoint)</option>
                    <option value="TRIPWIRE">TRIPWIRE (Boundary Crossing)</option>
                    <option value="EXCLUSION">EXCLUSION (No-Fly Zone)</option>
                  </select>
                </div>

                <div>
                  <label style={{ fontSize: 11, color: 'var(--text-secondary)' }}>Tactical Color:</label>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 3 }}>
                    <input
                      type="color"
                      value={zoneColor}
                      onChange={(e) => setZoneColor(e.target.value)}
                      style={{
                        width: 32,
                        height: 30,
                        padding: 0,
                        border: 'none',
                        background: 'transparent',
                        cursor: 'pointer',
                      }}
                    />
                    <span style={{ fontSize: 11, fontFamily: 'var(--font-mono)', color: zoneColor }}>
                      {zoneColor.toUpperCase()}
                    </span>
                  </div>
                </div>
              </div>

              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11 }}>
                  <span style={{ color: 'var(--text-secondary)' }}>Threat Severity Multiplier:</span>
                  <b style={{ color: '#00f0ff' }}>{(severity * 100).toFixed(0)}%</b>
                </div>
                <input
                  type="range"
                  min="0.1"
                  max="1.0"
                  step="0.05"
                  value={severity}
                  onChange={(e) => setSeverity(parseFloat(e.target.value))}
                  style={{ width: '100%', marginTop: 4, accentColor: '#00f0ff' }}
                />
              </div>

              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11 }}>
                  <span style={{ color: 'var(--text-secondary)' }}>Dwell / Loiter Alert Threshold:</span>
                  <b style={{ color: '#ffaa00' }}>{dwellThreshold} seconds</b>
                </div>
                <input
                  type="range"
                  min="5"
                  max="120"
                  step="5"
                  value={dwellThreshold}
                  onChange={(e) => setDwellThreshold(parseInt(e.target.value))}
                  style={{ width: '100%', marginTop: 4, accentColor: '#ffaa00' }}
                />
              </div>

              <button
                className="btn btn-primary"
                onClick={handleSave}
                disabled={isSaving || activePoints.length < 3}
                style={{
                  fontWeight: 800,
                  letterSpacing: 1,
                  padding: '10px 16px',
                  marginTop: 6,
                }}
              >
                {isSaving ? 'Syncing to Pipeline...' : '💾 Save Zone to Edge Pipeline'}
              </button>
            </div>

            <hr style={{ borderColor: 'rgba(255, 255, 255, 0.08)', margin: '4px 0' }} />

            {/* Active Zones List */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, flex: 1, minHeight: 0 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-secondary)', letterSpacing: 1 }}>
                  ACTIVE ZONES ON THIS NODE
                </span>
                <span className="badge" style={{ background: 'rgba(0, 240, 255, 0.1)', color: '#00f0ff' }}>
                  {existingZones.length}
                </span>
              </div>

              {existingZones.length === 0 ? (
                <div style={{ fontSize: 11, color: 'var(--text-ghost)', fontStyle: 'italic', padding: 8 }}>
                  No perimeter zones configured yet. Plot points above to create one.
                </div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6, overflowY: 'auto', maxHeight: 220 }}>
                  {existingZones.map((z) => (
                    <div
                      key={z.id}
                      style={{
                        padding: '8px 10px',
                        background: 'rgba(255, 255, 255, 0.02)',
                        border: `1px solid ${z.color ? z.color + '40' : 'rgba(255, 255, 255, 0.08)'}`,
                        borderRadius: 4,
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                      }}
                    >
                      <div>
                        <div style={{ fontSize: 12, fontWeight: 600, color: z.color || '#fff' }}>
                          {z.name}
                        </div>
                        <div style={{ fontSize: 10, color: 'var(--text-ghost)', marginTop: 2 }}>
                          {z.zone_type} • Dwell: {z.dwell_threshold_seconds}s • Sev: {(z.severity * 100).toFixed(0)}%
                        </div>
                      </div>
                      <button
                        className="btn btn-sm btn-danger"
                        style={{ padding: '2px 6px', fontSize: 11 }}
                        onClick={() => handleDeleteZone(z.id)}
                        title="Delete zone"
                      >
                        🗑️
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
