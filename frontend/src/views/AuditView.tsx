import React, { useState, useEffect } from "react";
import { api } from "../api";
import type { AuditLog } from "../types";
import { playTacticalTone, fmtTime } from "../utils/audio";
import { useTacticalStore } from "../store/useTacticalStore";

function Empty({ text }: { text: string }) {
  return (
    <div style={{ textAlign: "center", padding: "40px 10px", color: "var(--text-ghost)", fontSize: 12 }}>
      {text}
    </div>
  );
}

export function AuditView() {
  const store = useTacticalStore();
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [chainValid, setChainValid] = useState<boolean | null>(null);
  const [verifying, setVerifying] = useState(false);
  const [loading, setLoading] = useState(true);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [isForbidden, setIsForbidden] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [verifiedCount, setVerifiedCount] = useState<number | null>(null);

  const fetchLogs = async () => {
    setLoading(true);
    setErrorMsg(null);
    setIsForbidden(false);
    try {
      const res = await api.auditLogs(200);
      setLogs(Array.isArray(res) ? res : []);
    } catch (err: any) {
      const msg = err?.message || String(err);
      if (msg.includes("403") || msg.includes("view_audit")) {
        setIsForbidden(true);
        setErrorMsg("Clearance Restriction: Military audit ledger requires COMMANDER, ADMIN, or AUDITOR role under RBAC separation of duties.");
      } else if (msg.includes("401")) {
        setErrorMsg("Authentication Required: Please log in with authorized credentials.");
      } else {
        setErrorMsg(msg);
      }
      setLogs([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchLogs();
  }, [store.currentUser]);

  const verifyChain = async () => {
    setVerifying(true);
    playTacticalTone('click');
    setErrorMsg(null);
    try {
      const res = await api.verifyAudit();
      setChainValid(res.chain_valid);
      setVerifiedCount(res.entries_count || logs.length);
      playTacticalTone(res.chain_valid ? 'verify' : 'alert');
    } catch (err: any) {
      const msg = err?.message || String(err);
      if (msg.includes("403")) {
        setErrorMsg("Clearance Restriction: Chain verification requires COMMANDER, ADMIN, or AUDITOR role.");
      } else {
        setErrorMsg("Verification request failed: " + msg);
      }
      setChainValid(false);
      playTacticalTone('alert');
    } finally {
      setVerifying(false);
    }
  };

  const filteredLogs = logs.filter((l) => {
    if (!l) return false;
    if (!searchQuery.trim()) return true;
    const q = searchQuery.toLowerCase();
    const action = (l.action || "").toLowerCase();
    const actor = (l.actor || "").toLowerCase();
    const role = (l.actor_role || (l as any).role || "").toLowerCase();
    const targetType = (l.target_type || "").toLowerCase();
    const targetId = (l.target_id || "").toLowerCase();
    return (
      action.includes(q) ||
      actor.includes(q) ||
      role.includes(q) ||
      targetType.includes(q) ||
      targetId.includes(q)
    );
  });

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <h1>Tamper-Proof Military Audit Ledger</h1>
            <span
              className="reason-tag"
              style={{
                background: isForbidden ? 'rgba(255, 42, 85, 0.15)' : 'rgba(0, 240, 255, 0.15)',
                color: isForbidden ? '#ff2a55' : '#00f0ff',
                borderColor: isForbidden ? '#ff2a55' : '#00f0ff',
              }}
            >
              {isForbidden ? 'RESTRICTED CLEARANCE' : 'ACTIVE LEDGER'}
            </span>
          </div>
          <p style={{ margin: 0, fontSize: 12, color: 'var(--text-secondary)' }}>
            Cryptographically chained immutable ledger recording all officer and AI system actions (ISO 27037 & BSA §63)
          </p>
        </div>
        <div style={{ display: 'flex', gap: 10 }}>
          <button className="btn btn-secondary" onClick={fetchLogs} disabled={loading}>
            {loading ? 'Refreshing...' : '🔄 Refresh'}
          </button>
          <button className="btn btn-primary" onClick={verifyChain} disabled={verifying || loading || isForbidden}>
            {verifying ? 'Verifying Hash Chain...' : '🔗 Verify Chain Integrity'}
          </button>
        </div>
      </div>

      {/* Clearance Guard Banner */}
      {isForbidden && (
        <div
          style={{
            background: 'rgba(255, 170, 0, 0.12)',
            border: '1px solid #ffaa00',
            borderRadius: 6,
            padding: 16,
            marginBottom: 20,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 16,
          }}
        >
          <div>
            <div style={{ color: '#ffaa00', fontWeight: 'bold', fontSize: 13, marginBottom: 4 }}>
              🔒 SECURITY CLEARANCE LEVEL RESTRICTION (RBAC ENFORCED)
            </div>
            <div style={{ color: '#ddd', fontSize: 12 }}>
              The current user session (<b>{store.currentUser?.username || 'operator'}</b> with role{' '}
              <b style={{ color: '#00f0ff' }}>{store.currentUser?.role || 'OPERATOR'}</b>) lacks the{' '}
              <code style={{ background: '#111', padding: '2px 6px', borderRadius: 4, color: '#ffaa00' }}>view_audit</code>{' '}
              permission. In defense deployments, audit records are strictly restricted to{' '}
              <b>COMMANDER</b>, <b>ADMIN</b>, or <b>AUDITOR</b> roles.
            </div>
          </div>
          <button
            className="btn btn-primary"
            style={{ whiteSpace: 'nowrap', padding: '8px 14px' }}
            onClick={() => store.setShowLoginModal(true)}
          >
            🛡️ Switch Role to Commander / Admin
          </button>
        </div>
      )}

      {errorMsg && !isForbidden && (
        <div className="test-feedback fail" style={{ marginBottom: 16 }}>
          ⚠️ {errorMsg}
        </div>
      )}

      {chainValid !== null && (
        <div className={`test-feedback ${chainValid ? 'success' : 'fail'}`} style={{ marginBottom: 16 }}>
          {chainValid
            ? `✓ IMMUTABLE AUDIT CHAIN 100% VALID — ${verifiedCount || logs.length} BLOCKS VERIFIED (ZERO TAMPERING DETECTED)`
            : '✕ AUDIT CHAIN INTEGRITY CORRUPTED'}
        </div>
      )}

      {/* Search & Filter Toolbar */}
      {!isForbidden && (
        <div style={{ display: 'flex', gap: 12, marginBottom: 16, alignItems: 'center' }}>
          <input
            type="text"
            className="tactical-input"
            placeholder="Filter audit entries by action, actor, target..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            style={{
              maxWidth: 380,
              background: '#09111e',
              border: '1px solid rgba(0, 240, 255, 0.3)',
              borderRadius: 4,
              padding: '6px 12px',
              fontSize: 12,
              color: '#fff',
            }}
          />
          <span style={{ fontSize: 11, color: 'var(--text-ghost)', fontFamily: 'var(--font-mono)' }}>
            Showing {filteredLogs.length} of {logs.length} blocks
          </span>
        </div>
      )}

      {loading ? (
        <div style={{ textAlign: 'center', padding: '60px 20px', color: 'var(--text-ghost)', fontSize: 13 }}>
          <div style={{ marginBottom: 10 }}>⏳ Loading cryptographic audit ledger...</div>
        </div>
      ) : filteredLogs.length === 0 ? (
        <Empty text={isForbidden ? "Audit trail locked. Authenticate as Commander or Admin to inspect blocks." : "Audit ledger empty."} />
      ) : (
        <div className="blockchain-ledger-view">
          {filteredLogs.map((l) => {
            const role = l.actor_role || (l as any).role || 'OPERATOR';
            const hash = l.entry_hash || (l as any).record_hash || '';
            return (
              <div key={l.id} className="blockchain-block">
                <div className="block-index-badge">
                  <span style={{ fontSize: 9, color: 'var(--text-ghost)' }}>BLOCK</span>
                  <span>#{l.id}</span>
                </div>
                <div className="block-data-col">
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 4, flexWrap: 'wrap' }}>
                    <b style={{ color: '#fff', fontSize: 13 }}>{l.action}</b>
                    <span className="reason-tag" style={{ background: 'rgba(0, 240, 255, 0.1)', color: '#00f0ff' }}>
                      {l.actor} ({role})
                    </span>
                    {l.target_type && (
                      <span className="reason-tag" style={{ background: 'rgba(255, 255, 255, 0.05)', color: '#aaa' }}>
                        {l.target_type}:{l.target_id || '-'}
                      </span>
                    )}
                    <span style={{ marginLeft: 'auto', fontSize: 10, color: 'var(--text-ghost)', fontFamily: 'var(--font-mono)' }}>
                      {fmtTime(l.created_at)}
                    </span>
                  </div>
                  {l.details && Object.keys(l.details).length > 0 && (
                    <div style={{ fontSize: 11, color: '#8fa0b5', marginBottom: 4, fontFamily: 'var(--font-mono)' }}>
                      {JSON.stringify(l.details)}
                    </div>
                  )}
                  <div className="block-hash-pipe">
                    <span>
                      PREV: <span className="hash-token">{l.previous_hash ? String(l.previous_hash).substring(0, 16) : 'GENESIS'}…</span>
                    </span>
                    <span>→</span>
                    <span>
                      HASH: <span className="hash-token">{hash ? String(hash).substring(0, 16) : '—'}…</span>
                    </span>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
