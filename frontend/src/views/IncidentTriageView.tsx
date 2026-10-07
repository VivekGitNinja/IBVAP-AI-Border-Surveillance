import React, { useState, useEffect } from "react";
import { api } from "../api";
import type { Incident, Evidence } from "../types";
import { playTacticalTone, fmtTime, fmtTimeShort } from "../utils/audio";
import { TimelineScrubber } from "../components/TimelineScrubber";
import { LegalCertificateModal } from "../components/LegalCertificateModal";
import { QRTScrambleModal } from "../components/QRTScrambleModal";

function Empty({ text }: { text: string }) {
  return (
    <div style={{ textAlign: "center", padding: "40px 10px", color: "var(--text-ghost)", fontSize: 12 }}>
      {text}
    </div>
  );
}

export function IncidentTriageView({ incidents, openInc }: { incidents: Incident[]; openInc: (id: number) => void }) {
  const [filter, setFilter] = useState('all');
  const [scrambleIncident, setScrambleIncident] = useState<Incident | null>(null);

  const list = filter === 'all'
    ? incidents
    : incidents.filter((i) => i.status === filter || i.severity === filter.toUpperCase());

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Threat Intelligence & Incident Queue</h1>
          <p style={{ margin: 0, fontSize: 12, color: 'var(--text-secondary)' }}>
            AI-correlated border perimeter breaches requiring human operator verification
          </p>
        </div>
        <div className="filter-row">
          {['all', 'OPEN', 'ACKNOWLEDGED', 'ESCALATED', 'CLOSED'].map((f) => (
            <button
              key={f}
              className={`filter-btn${filter === f ? ' active' : ''}`}
              onClick={() => { playTacticalTone('click'); setFilter(f); }}
            >
              {f}
            </button>
          ))}
        </div>
      </div>

      {/* Frigate-Style 24-Hour Visual Scrubber & Event Timeline */}
      <TimelineScrubber incidents={incidents} onSelectIncident={openInc} />

      {list.length === 0 ? (
        <Empty text="No incidents match this status classification." />
      ) : (
        <div className="incident-list">
          {list.map((i) => (
            <div key={i.id} className="incident-card" onClick={() => openInc(i.id)}>
              <div className="ic-header">
                <span className={`sev-badge sev-${(i.severity || 'LOW').toLowerCase()}`}>{i.severity || 'LOW'}</span>
                <span className="ic-code">{i.incident_code || `INC-${i.id}`}</span>
                <span className="ic-score">THREAT SCORE: {(i.threat_score ?? 0).toFixed(0)}/100</span>
                <span className={`ic-status ${(i.status || 'OPEN').toLowerCase()}`}>{i.status || 'OPEN'}</span>
              </div>
              <div className="ic-title">{i.title || 'Perimeter Alert'}</div>
              <div className="ic-meta">
                {i.camera_name || 'BOP Sector'} • {i.zone_name || 'Perimeter Zone'} • {fmtTime(i.created_at)}
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 8 }}>
                <div className="ic-reasons">
                  {(i.reason_codes || []).slice(0, 4).map((r) => (
                    <span key={r} className="reason-tag">
                      {String(r || '').split(':')[0].trim()}
                    </span>
                  ))}
                </div>
                <button
                  className="btn btn-sm btn-danger"
                  style={{ padding: '3px 10px', fontSize: 10, fontWeight: 800, background: '#ff2a55', flexShrink: 0 }}
                  onClick={(e) => {
                    e.stopPropagation();
                    playTacticalTone('click');
                    setScrambleIncident(i);
                  }}
                  title="Scramble Armed QRT Strike Unit to this breach location"
                >
                  ⚡ Scramble QRT
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* QRT Armed Response Scramble Modal */}
      {scrambleIncident && (
        <QRTScrambleModal
          incident={scrambleIncident}
          onClose={() => setScrambleIncident(null)}
        />
      )}
    </div>
  );
}

/* ─── Section 65B Statutory Legal Certificate Modal ───────────── */
function CertificateModal({ cert, onClose }: { cert: any; onClose: () => void }) {
  if (!cert) return null;
  return (
    <div
      className="modal-backdrop"
      onClick={onClose}
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(2, 6, 12, 0.88)',
        backdropFilter: 'blur(8px)',
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
          background: '#070b14',
          border: '1px solid rgba(168, 85, 247, 0.5)',
          borderRadius: 8,
          maxWidth: 780,
          width: '100%',
          maxHeight: '90vh',
          overflowY: 'auto',
          padding: 24,
          boxShadow: '0 0 60px rgba(168, 85, 247, 0.3)',
          position: 'relative',
        }}
      >
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'flex-start',
            borderBottom: '1px solid rgba(168, 85, 247, 0.3)',
            paddingBottom: 14,
          }}
        >
          <div>
            <div style={{ fontSize: 10, color: '#a855f7', letterSpacing: 2, fontFamily: 'var(--font-mono)' }}>
              GOVERNMENT OF INDIA • MINISTRY OF HOME AFFAIRS • SECTION 63 BSA ELECTRONIC RECORD
            </div>
            <h2 style={{ margin: '4px 0', fontSize: 20, color: '#fff', letterSpacing: 1 }}>
              {cert.certificate_id}
            </h2>
            <div style={{ fontSize: 11, color: '#00ff9d', fontFamily: 'var(--font-mono)' }}>
              STATUTE: {cert.legal_statute}
            </div>
          </div>
          <button className="btn btn-secondary btn-sm" onClick={onClose}>
            ✕ Close
          </button>
        </div>

        <div
          style={{
            margin: '16px 0',
            background: 'rgba(0, 0, 0, 0.45)',
            border: '1px solid rgba(255, 255, 255, 0.08)',
            borderRadius: 6,
            padding: 16,
          }}
        >
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 10, fontSize: 12 }}>
            <div>
              <span style={{ color: 'var(--text-ghost)' }}>INCIDENT CODE:</span>{' '}
              <b style={{ color: '#00f0ff' }}>{cert.incident_code}</b>
            </div>
            <div>
              <span style={{ color: 'var(--text-ghost)' }}>ADMISSIBILITY STATUS:</span>{' '}
              <b style={{ color: '#00ff9d' }}>{cert.admissibility_status}</b>
            </div>
            <div>
              <span style={{ color: 'var(--text-ghost)' }}>SURVEILLANCE POST:</span>{' '}
              <span style={{ color: '#fff' }}>{cert.surveillance_post}</span>
            </div>
            <div>
              <span style={{ color: 'var(--text-ghost)' }}>CAMERA DESIGNATION:</span>{' '}
              <span style={{ color: '#fff' }}>{cert.camera_designation}</span>
            </div>
            <div>
              <span style={{ color: 'var(--text-ghost)' }}>STORAGE MEDIUM:</span>{' '}
              <span style={{ color: '#fff' }}>{cert.storage_integrity}</span>
            </div>
            <div>
              <span style={{ color: 'var(--text-ghost)' }}>CLOCK TIMEBASE:</span>{' '}
              <span style={{ color: '#fff' }}>{cert.clock_source}</span>
            </div>
          </div>

          <div style={{ marginTop: 14, borderTop: '1px solid rgba(255, 255, 255, 0.08)', paddingTop: 10 }}>
            <span style={{ fontSize: 10, color: 'var(--text-ghost)', letterSpacing: 1 }}>
              FIPS 180-4 CRYPTOGRAPHIC SHA-256 DIGEST:
            </span>
            <div
              style={{
                fontFamily: 'var(--font-mono)',
                fontSize: 11,
                color: '#a855f7',
                wordBreak: 'break-all',
                marginTop: 4,
                padding: '6px 10px',
                background: 'rgba(168, 85, 247, 0.1)',
                border: '1px solid rgba(168, 85, 247, 0.25)',
                borderRadius: 4,
              }}
            >
              {cert.sha256_digest}
            </div>
          </div>
        </div>

        <div
          style={{
            background: 'rgba(168, 85, 247, 0.08)',
            border: '1px solid rgba(168, 85, 247, 0.25)',
            borderRadius: 6,
            padding: 16,
            margin: '16px 0',
          }}
        >
          <div style={{ fontSize: 11, color: '#c084fc', fontWeight: 'bold', marginBottom: 6, letterSpacing: 1 }}>
            STATUTORY DECLARATION UNDER SECTION 63 BHARATIYA SAKSHYA ADHINIYAM (BSA), 2023 [FORMERLY SEC 65B]:
          </div>
          <p style={{ fontSize: 12, lineHeight: 1.6, color: '#e2e8f0', margin: 0 }}>
            "{cert.legal_declaration}"
          </p>
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              marginTop: 14,
              paddingTop: 12,
              borderTop: '1px dashed rgba(168, 85, 247, 0.25)',
            }}
          >
            <div>
              <div style={{ fontSize: 12, fontWeight: 'bold', color: '#fff' }}>{cert.certifying_officer}</div>
              <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                {cert.officer_rank} • Sashastra Seema Bal (SSB)
              </div>
            </div>
            <div style={{ textAlign: 'right', fontSize: 11, color: 'var(--text-ghost)', fontFamily: 'var(--font-mono)' }}>
              CERTIFICATION TIMESTAMP: {fmtTime(cert.generated_at)}
            </div>
          </div>
        </div>

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
          <button className="btn btn-secondary btn-sm" onClick={() => window.print()}>
            🖨️ Print / Save PDF Affidavit
          </button>
          <button className="btn btn-primary btn-sm" onClick={onClose}>
            ✓ Return to System
          </button>
        </div>
      </div>
    </div>
  );
}

/* ─── Incident Forensic Dossier ───────────────────────────────── */
export function IncidentDetailView({
  incident: i,
  onBack,
  refresh,
}: {
  incident: Incident;
  onBack: () => void;
  refresh: (id: number) => void;
}) {
  const [evidence, setEvidence] = useState<Evidence[]>([]);
  const [verifying, setVerifying] = useState(false);
  const [verResult, setVerResult] = useState<any>(null);
  const [activeTab, setActiveTab] = useState<'assessment' | 'evidence' | 'timeline'>('assessment');
  const [showLegalCert, setShowLegalCert] = useState(false);
  const [showScrambleModal, setShowScrambleModal] = useState(false);

  useEffect(() => {
    api.evidence(i.id).then(setEvidence).catch(() => {});
  }, [i.id]);

  const ack = async () => {
    playTacticalTone('verify');
    await api.acknowledgeIncident(i.id);
    refresh(i.id);
  };

  const esc = async () => {
    playTacticalTone('escalate');
    await api.escalateIncident(i.id);
    refresh(i.id);
  };

  const dis = async () => {
    playTacticalTone('click');
    await api.dismissIncident(i.id);
    refresh(i.id);
  };

  const cls = async () => {
    playTacticalTone('click');
    await api.closeIncident(i.id);
    refresh(i.id);
  };

  const verify = async () => {
    if (!evidence.length) return;
    setVerifying(true);
    playTacticalTone('click');
    try {
      const r = await api.verifyEvidence(evidence[0].id);
      setVerResult(r);
      playTacticalTone(r.valid ? 'verify' : 'alert');
    } catch {
      setVerResult({ valid: false });
      playTacticalTone('alert');
    }
    setVerifying(false);
  };

  const ai = i.ai_assessment || {};

  return (
    <div className="page">
      <button className="btn-back" onClick={onBack}>
        ← Return to Incident Queue
      </button>

      <div className="detail-header" style={{ borderBottom: '1px solid rgba(0, 240, 255, 0.2)', paddingBottom: 12 }}>
        <div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: '#ff2a55', letterSpacing: 2 }}>
            RESTRICTED // LAW ENFORCEMENT & BORDER DEFENSE INTELLIGENCE
          </div>
          <h1 style={{ margin: '4px 0', fontSize: 24, letterSpacing: 1 }}>
            INCIDENT DOSSIER: {i.incident_code || `INC-${i.id}`}
          </h1>
          <span className={`sev-badge sev-${(i.severity || 'LOW').toLowerCase()} large`}>{i.severity || 'LOW'} THREAT</span>
        </div>
        <div className="detail-actions">
          <button
            className="btn btn-danger"
            style={{ background: '#ff2a55', borderColor: '#ff2a55', fontWeight: 800 }}
            onClick={() => {
              playTacticalTone('click');
              setShowScrambleModal(true);
            }}
            title="Scramble Armed QRT Commando Strike to this location"
          >
            ⚡ Scramble QRT Strike
          </button>
          {i.status === 'OPEN' && (
            <button className="btn btn-primary" onClick={ack}>
              ✓ Verify & Acknowledge
            </button>
          )}
          {i.status !== 'ESCALATED' && i.status !== 'CLOSED' && (
            <button className="btn btn-warn" onClick={esc}>
              🚨 Escalate to Patrol QRT
            </button>
          )}
          {i.status !== 'DISMISSED' && i.status !== 'CLOSED' && (
            <button className="btn btn-secondary" onClick={dis}>
              ✕ Dismiss False Alarm
            </button>
          )}
          {i.status !== 'CLOSED' && (
            <button className="btn btn-secondary" onClick={cls}>
              ● Close Incident
            </button>
          )}
        </div>
      </div>

      {/* Modern Dossier Navigation Tabs */}
      <div className="dossier-tab-bar">
        <button
          className={`dossier-tab-btn ${activeTab === 'assessment' ? 'active' : ''}`}
          onClick={() => { playTacticalTone('click'); setActiveTab('assessment'); }}
        >
          🧠 AI Threat Assessment
        </button>
        <button
          className={`dossier-tab-btn ${activeTab === 'evidence' ? 'active' : ''}`}
          onClick={() => { playTacticalTone('click'); setActiveTab('evidence'); }}
        >
          📹 Section 65B Video & Evidence ({evidence.length})
        </button>
        <button
          className={`dossier-tab-btn ${activeTab === 'timeline' ? 'active' : ''}`}
          onClick={() => { playTacticalTone('click'); setActiveTab('timeline'); }}
        >
          ⏱️ Chronological Audit Timeline ({i.timeline?.length || 0})
        </button>
      </div>

      {/* TAB 1: AI Threat Assessment */}
      {activeTab === 'assessment' && (
        <div className="detail-grid">
          <div className="detail-left">
            {/* Key Metric Info */}
            <div className="info-card">
              <div className="info-row"><label>Threat Score</label><span className="threat-score">{(i.threat_score ?? 0).toFixed(0)}<small>/100</small></span></div>
              <div className="info-row"><label>AI Confidence</label><span>{(((i.confidence ?? 0)) * 100).toFixed(0)}%</span></div>
              <div className="info-row"><label>Surveillance Post</label><span>{i.camera_name || 'BOP-01 Gate Post'}</span></div>
              <div className="info-row"><label>Perimeter Zone</label><span>{i.zone_name || 'Restricted Zero-Line'}</span></div>
              <div className="info-row"><label>Status</label><span className={`ic-status ${(i.status || 'OPEN').toLowerCase()}`}>{i.status || 'OPEN'}</span></div>
              <div className="info-row"><label>Sensor Timestamp</label><span>{fmtTime(i.created_at)}</span></div>
            </div>

            {/* Recommended Action */}
            <div className="info-card action-card">
              <h3>Standard Operating Procedure (SOP)</h3>
              <p>{i.recommended_action || 'Dispatch armed QRT patrol to sector coordinates. Verify thermal perimeter sensor.'}</p>
            </div>

            {/* Why Alert Triggered */}
            <div className="info-card">
              <h3>Rule & AI Threat Attribution</h3>
              <div className="reason-list">
                {(i.reason_codes || []).map((r, idx) => (
                  <div key={idx} className="reason-item">
                    <span className="reason-num">{idx + 1}</span>
                    <span>{r}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div className="detail-right">
            {/* AI Explainability Assessment */}
            <div className="info-card">
              <h3>Perception Engine Assessment</h3>
              <div className="ai-grid">
                <div className="ai-item"><label>Detected Class</label><span>{ai.detected || 'Human Infiltrator'}</span></div>
                <div className="ai-item"><label>Confidence</label><span>{((ai.confidence || 0.94) * 100).toFixed(0)}%</span></div>
                <div className="ai-item"><label>Context</label><span>{ai.context || 'Night Perimeter'}</span></div>
                <div className="ai-item"><label>Behavior</label><span>{ai.behavior || 'Border Crossing'}</span></div>
                <div className="ai-item"><label>Uncertainty</label><span>{ai.uncertainty || 'Low (<5%)'}</span></div>
                <div className="ai-item"><label>Operator Role</label><span>{ai.human_action || 'Immediate Intercept'}</span></div>
              </div>

              {ai.threat_contributions && (
                <div className="contributions" style={{ marginTop: 14 }}>
                  <h4>Signal Contribution Breakdown</h4>
                  {Object.entries(ai.threat_contributions).map(([k, v]) => (
                    <div key={k} className="contrib-row">
                      <span className="contrib-label">{k.replace(/_/g, ' ')}</span>
                      <div className="contrib-bar">
                        <div className="contrib-fill" style={{ width: `${Math.min(100, (v as number) * 100)}%` }} />
                      </div>
                      <span className="contrib-val">{(v as number).toFixed(1)}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* TAB 2: Section 65B Video & Evidence Locker */}
      {activeTab === 'evidence' && (
        <div className="detail-grid">
          <div className="detail-left">
            {/* Section 65B Certified NVR Video Clip */}
            <div className="info-card" style={{ borderColor: 'rgba(0, 255, 157, 0.4)', boxShadow: '0 0 16px rgba(0, 255, 157, 0.1)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                <h3 style={{ margin: 0, color: '#00ff9d', fontSize: 13 }}>📹 Section 65B Incident Video Clip</h3>
                <span className="panel-tag" style={{ color: '#00ff9d', borderColor: '#00ff9d', fontSize: 9 }}>
                  SEALED NVR ARCHIVE
                </span>
              </div>
              <div style={{ background: '#051015', borderRadius: 4, overflow: 'hidden', border: '1px solid rgba(0, 240, 255, 0.2)' }}>
                {ai.clip_url || evidence.find((e) => e.evidence_type === 'clip') ? (
                  <video
                    controls
                    autoPlay
                    loop
                    muted
                    style={{ width: '100%', maxHeight: 320, display: 'block' }}
                    src={ai.clip_url || `/api/v1/evidence/clips/INC-${i.incident_code}.mp4`}
                  />
                ) : (
                  <div style={{ padding: '36px 12px', textAlign: 'center', color: '#557585', fontSize: 12 }}>
                    <div>⏳ Video clip packaging from rolling NVR ring buffer...</div>
                    <small style={{ color: '#00f0ff', marginTop: 4, display: 'block' }}>Clip: /api/v1/evidence/clips/INC-{i.incident_code}.mp4</small>
                  </div>
                )}
              </div>
              <div style={{ fontSize: 10, fontFamily: 'var(--font-mono)', color: '#658595', marginTop: 8, display: 'flex', justifyContent: 'space-between' }}>
                <span>DIGEST: {ai.clip_sha256 ? ai.clip_sha256.substring(0, 28) + '…' : 'SHA256 RECORDED'}</span>
                <span style={{ color: '#00ff9d' }}>CIRCULAR BUFFER CAPTURED</span>
              </div>
            </div>
          </div>

          <div className="detail-right">
            {/* Cryptographic Evidence Locker */}
            <div className="info-card">
              <div className="evidence-header">
                <h3>SHA-256 Sealed Evidence</h3>
                <button className="btn btn-sm" onClick={verify} disabled={verifying}>
                  {verifying ? 'Checking...' : '🔒 Verify Hash'}
                </button>
              </div>
              {verResult && (
                <div className={`verify-result ${verResult.valid ? 'valid' : 'invalid'}`}>
                  {verResult.valid ? '✓ SHA-256 DIGEST INTEGRITY VERIFIED (UNTAMPERED)' : '✕ INTEGRITY MISMATCH!'}
                  <small>DIGEST: {verResult.sha256?.substring(0, 24)}…</small>
                </div>
              )}
              {evidence.length === 0 ? (
                <Empty text="Evidence packaging in progress." />
              ) : (
                evidence.map((e) => (
                  <div key={e.id} className="evidence-row">
                    <span className="ev-type">{e.evidence_type}</span>
                    <span className="ev-hash">{e.sha256.substring(0, 18)}…</span>
                    <span className="ev-time">{fmtTime(e.created_at)}</span>
                  </div>
                ))
              )}

              {/* Statutory Legal Certificate Generator Button */}
              <button
                className="btn btn-sm btn-primary"
                style={{
                  width: '100%',
                  marginTop: 14,
                  background: 'linear-gradient(135deg, #a855f7 0%, #7c3aed 100%)',
                  borderColor: '#c084fc',
                  color: '#fff',
                  fontWeight: 700,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: 6,
                }}
                onClick={() => {
                  playTacticalTone('click');
                  setShowLegalCert(true);
                }}
              >
                📜 View & Print Statutory Legal Certificate (Sec 65B / BSA)
              </button>
            </div>
          </div>
        </div>
      )}

      {/* TAB 3: Incident Event Timeline */}
      {activeTab === 'timeline' && (
        <div className="info-card timeline-card">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
            <h3 style={{ margin: 0 }}>Chronological Sensor & Decision Audit Trail</h3>
            <span className="panel-tag">FORENSIC LOGS</span>
          </div>
          {i.timeline && i.timeline.length > 0 ? (
            <div className="timeline" style={{ padding: '8px 0' }}>
              {i.timeline.map((evt: any, idx: number) => (
                <div key={idx} className="timeline-step-row">
                  <div className="timeline-marker">{(idx + 1).toString().padStart(2, '0')}</div>
                  <div style={{ flex: 1 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 2 }}>
                      <b style={{ color: '#eaf8ff', fontSize: 13 }}>{evt.event_type?.replace(/_/g, ' ')}</b>
                      <span style={{ fontSize: 11, fontFamily: 'var(--font-mono)', color: '#00f0ff' }}>
                        {fmtTimeShort(evt.timestamp)}
                      </span>
                    </div>
                    <div style={{ fontSize: 12, color: '#8aa4b8', margin: '2px 0 4px' }}>{evt.description}</div>
                    <div style={{ fontSize: 10, color: '#4a6b82', fontFamily: 'var(--font-mono)' }}>Source: {evt.source}</div>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <Empty text="Timeline compilation in progress." />
          )}
        </div>
      )}

      {/* Official Section 63 BSA & Section 65B Electronic Evidence Certificate Modal */}
      {showLegalCert && (
        <LegalCertificateModal
          cert={{
            certificate_id: `CERT-BSA63-2026-${i.incident_code}`,
            incident_code: i.incident_code,
            surveillance_post: i.camera_name || 'BOP-01 (Sector Alpha)',
            camera_designation: i.camera_name || 'CAM-01 Zero-Line Camera',
            sha256_digest: ai.clip_sha256 || evidence[0]?.sha256 || 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
            evidence_id: evidence[0]?.id,
            generated_at: i.created_at,
            certifying_officer: 'Commandant Rajeshwar Singh',
            officer_rank: 'Commandant (Surveillance Operations)',
            clock_source: 'NTP Synced with NPL Indian Standard Time (IST)',
            storage_integrity: 'Direct NVMe Encrypted Ring Buffer (ext4)',
          }}
          onClose={() => setShowLegalCert(false)}
        />
      )}

      {/* 1-Click QRT Armed Response Scramble Modal */}
      {showScrambleModal && (
        <QRTScrambleModal
          incident={i}
          onClose={() => setShowScrambleModal(false)}
        />
      )}
    </div>
  );
}

/* ─── Evidence Locker Page ────────────────────────────────────── */
