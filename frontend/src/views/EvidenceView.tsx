import React, { useState, useEffect } from "react";
import { api } from "../api";
import type { Incident, Evidence } from "../types";
import { playTacticalTone, fmtTime } from "../utils/audio";
import { LegalCertificateModal } from "../components/LegalCertificateModal";

function Empty({ text }: { text: string }) {
  return (
    <div style={{ textAlign: "center", padding: "40px 10px", color: "var(--text-ghost)", fontSize: 12 }}>
      {text}
    </div>
  );
}

export function EvidenceView({ incidents }: { incidents: Incident[] }) {
  const [allEvidence, setAllEvidence] = useState<Evidence[]>([]);
  const [verifyingId, setVerifyingId] = useState<number | null>(null);
  const [results, setResults] = useState<Record<number, any>>({});
  const [selectedCert, setSelectedCert] = useState<Evidence | null>(null);
  const [selectedEvidence, setSelectedEvidence] = useState<Evidence | null>(null);

  useEffect(() => {
    Promise.all(incidents.slice(0, 8).map((inc) => api.evidence(inc.id).catch(() => []))).then((res) => {
      const flat = res.flat();
      flat.sort((a, b) => {
        const aClip = a.evidence_type === "clip" || a.file_path?.endsWith(".mp4");
        const bClip = b.evidence_type === "clip" || b.file_path?.endsWith(".mp4");
        if (aClip && !bClip) return -1;
        if (!aClip && bClip) return 1;
        return b.id - a.id;
      });
      setAllEvidence(flat);
    });
  }, [incidents]);

  const verifyItem = async (id: number) => {
    setVerifyingId(id);
    playTacticalTone('click');
    try {
      const res = await api.verifyEvidence(id);
      setResults((prev) => ({ ...prev, [id]: res }));
      playTacticalTone(res.valid ? 'verify' : 'alert');
    } catch {
      setResults((prev) => ({ ...prev, [id]: { valid: false, match: false } }));
      playTacticalTone('alert');
    }
    setVerifyingId(null);
  };

  const getEvidenceSrc = (e: Evidence) => {
    if (!e.file_path) return `/api/v1/media/1/stream`;
    const fname = e.file_path.split("/").pop() || "";
    const token = (typeof localStorage !== 'undefined' ? (localStorage.getItem('ibvap_token') || localStorage.getItem('token')) : '') || '';
    return `/api/v1/evidence/vault/clips/${fname}?token=${encodeURIComponent(token)}`;
  };

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Forensic Cryptographic Evidence Locker</h1>
          <p style={{ margin: 0, fontSize: 12, color: 'var(--text-secondary)' }}>
            Tamper-evident surveillance artifacts sealed with SHA-256 cryptographic hashes; technical controls aligned with Bharatiya Sakshya Adhiniyam, 2023 Section 63 requirements
          </p>
        </div>
      </div>

      {allEvidence.length === 0 ? (
        <Empty text="No evidence sealed yet. Real surveillance artifacts appear after video analysis." />
      ) : (
        <div className="evidence-card-grid">
          {allEvidence.map((e) => {
            const vRes = results[e.id];
            const verified = vRes?.valid ?? false;
            return (
              <div
                key={e.id}
                className="evidence-locker-item"
                style={{ cursor: 'pointer' }}
                onClick={() => {
                  playTacticalTone('click');
                  setSelectedEvidence(e);
                }}
              >
                <div className="evidence-locker-header">
                  <div>
                    <b style={{ color: '#fff', fontSize: 13 }}>EVIDENCE RECORD #{e.id}</b>
                    <div style={{ fontSize: 10, color: 'var(--text-ghost)', fontFamily: 'var(--font-mono)' }}>
                      INCIDENT #{e.incident_id} • {e.camera_name || 'BOP Post'}
                    </div>
                  </div>
                  <span className="panel-tag" style={{ color: '#00f0ff', borderColor: '#00f0ff' }}>
                    {e.evidence_type}
                  </span>
                </div>

                <div className="sha256-hash-box">
                  <span style={{ color: 'var(--text-ghost)', fontSize: 9 }}>SHA-256 DIGEST:</span>
                  <div style={{ wordBreak: 'break-all', marginTop: 2 }}>{e.sha256}</div>
                </div>

                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 12 }}>
                  <span style={{ fontSize: 10, color: 'var(--text-secondary)' }}>{fmtTime(e.created_at)}</span>
                  <div style={{ display: 'flex', gap: 6 }} onClick={(ev) => ev.stopPropagation()}>
                    <button
                      className={`btn btn-sm ${verified ? 'btn-primary' : 'btn-secondary'}`}
                      onClick={() => verifyItem(e.id)}
                      disabled={verifyingId === e.id}
                      data-testid="evidence-verify-hash"
                    >
                      {verifyingId === e.id ? 'Checking...' : verified ? '✓ SEAL VALID' : '🔒 Verify Seal'}
                    </button>
                    <button
                      className="btn btn-sm btn-secondary"
                      onClick={() => { playTacticalTone('click'); setSelectedCert(e); }}
                      style={{ borderColor: '#00f0ff', color: '#00f0ff' }}
                    >
                      📜 BSA §63 Cert
                    </button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Interactive Evidence Video Player Modal */}
      {selectedEvidence && (
        <div
          className="section-65b-modal-backdrop"
          onClick={() => setSelectedEvidence(null)}
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(2, 6, 14, 0.9)',
            backdropFilter: 'blur(10px)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 9999,
            padding: 20,
          }}
        >
          <div
            className="panel"
            style={{ maxWidth: 720, width: '100%', margin: 0, padding: 20 }}
            onClick={(ev) => ev.stopPropagation()}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
              <div>
                <h3 style={{ margin: 0, color: '#00f0ff' }}>
                  EVIDENCE RECORD #{selectedEvidence.id} // SECURE PLAYBACK
                </h3>
                <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                  Statute: Technical controls aligned with Bharatiya Sakshya Adhiniyam, 2023 Section 63 requirements
                </div>
              </div>
              <button className="btn btn-sm btn-secondary" onClick={() => setSelectedEvidence(null)}>
                ✕ Close
              </button>
            </div>

            <div style={{ background: '#000', borderRadius: 4, overflow: 'hidden', border: '1px solid rgba(0, 240, 255, 0.3)', marginBottom: 12 }}>
              <video
                controls
                autoPlay
                muted
                playsInline
                preload="auto"
                style={{ width: '100%', maxHeight: 380, display: 'block' }}
                src={getEvidenceSrc(selectedEvidence)}
              />
            </div>

            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: 'rgba(0,0,0,0.3)', padding: 10, borderRadius: 4 }}>
              <div>
                <div style={{ fontSize: 10, color: 'var(--text-ghost)', fontFamily: 'var(--font-mono)' }}>SHA-256 DIGEST</div>
                <div style={{ fontSize: 11, color: '#00ff9d', fontFamily: 'var(--font-mono)', wordBreak: 'break-all' }}>
                  {selectedEvidence.sha256}
                </div>
              </div>
              <button
                className="btn btn-sm btn-primary"
                onClick={() => verifyItem(selectedEvidence.id)}
                disabled={verifyingId === selectedEvidence.id}
                style={{ minWidth: 120 }}
                data-testid="evidence-verify-hash"
              >
                {verifyingId === selectedEvidence.id ? 'Checking...' : '🔍 Verify Hash'}
              </button>
            </div>

            {results[selectedEvidence.id] && (
              <div
                style={{
                  marginTop: 10,
                  padding: '8px 12px',
                  borderRadius: 4,
                  background: results[selectedEvidence.id].match ? 'rgba(0, 255, 157, 0.1)' : 'rgba(255, 42, 85, 0.1)',
                  border: `1px solid ${results[selectedEvidence.id].match ? '#00ff9d' : '#ff2a55'}`,
                  color: results[selectedEvidence.id].match ? '#00ff9d' : '#ff2a55',
                  fontSize: 12,
                  fontWeight: 600,
                  display: 'flex',
                  justifyContent: 'space-between',
                }}
              >
                <span>
                  {results[selectedEvidence.id].match ? '✓ match: true (BSA 2023 §63 Authenticated)' : '✕ match: false (Hash Mismatch)'}
                </span>
                <span style={{ fontSize: 10, opacity: 0.8 }}>
                  Computed: {results[selectedEvidence.id].computed_hash?.substring(0, 16)}…
                </span>
              </div>
            )}
          </div>
        </div>
      )}

      {selectedCert && (
        <LegalCertificateModal cert={selectedCert} onClose={() => setSelectedCert(null)} />
      )}
    </div>
  );
}
