import React, { useState, useEffect, useRef } from "react";
import { api } from "../api";
import { playTacticalTone, fmtTime } from "../utils/audio";

function Empty({ text }: { text: string }) {
  return (
    <div style={{ textAlign: "center", padding: "40px 10px", color: "var(--text-ghost)", fontSize: 12 }}>
      {text}
    </div>
  );
}

export function FRSView({ openInc }: { openInc?: (id: number) => void }) {
  const [suspects, setSuspects] = useState<any[]>([]);
  const [matches, setMatches] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [showEnroll, setShowEnroll] = useState(false);
  const [name, setName] = useState("");
  const [notes, setNotes] = useState("");
  const [enrollFile, setEnrollFile] = useState<File | null>(null);
  const [enrollPreviewUrl, setEnrollPreviewUrl] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [probeFile, setProbeFile] = useState<File | null>(null);
  const [probePreviewUrl, setProbePreviewUrl] = useState<string | null>(null);
  const [isVerifyingProbe, setIsVerifyingProbe] = useState(false);
  const [probeResult, setProbeResult] = useState<any>(null);
  const [selectedMatch, setSelectedMatch] = useState<any | null>(null);

  // Live Webcam Snapshot Capture State
  const [webcamMode, setWebcamMode] = useState<'none' | 'enroll' | 'probe'>('none');
  const webcamVideoRef = useRef<HTMLVideoElement | null>(null);
  const webcamStreamRef = useRef<MediaStream | null>(null);

  const startWebcam = async (target: 'enroll' | 'probe') => {
    try {
      playTacticalTone('click');
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: 'user' },
        audio: false,
      });
      webcamStreamRef.current = stream;
      setWebcamMode(target);
      setTimeout(() => {
        if (webcamVideoRef.current) {
          webcamVideoRef.current.srcObject = stream;
          webcamVideoRef.current.play().catch(() => {});
        }
      }, 100);
    } catch (err: any) {
      alert("Webcam capture error or permission denied: " + err.message);
    }
  };

  const stopWebcam = () => {
    if (webcamStreamRef.current) {
      webcamStreamRef.current.getTracks().forEach((track) => track.stop());
      webcamStreamRef.current = null;
    }
    setWebcamMode('none');
  };

  const captureWebcamFrame = (target: 'enroll' | 'probe') => {
    if (!webcamVideoRef.current) return;
    const video = webcamVideoRef.current;
    const canvas = document.createElement('canvas');
    canvas.width = video.videoWidth || 640;
    canvas.height = video.videoHeight || 480;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);

    canvas.toBlob((blob) => {
      if (!blob) return;
      const file = new File([blob], `webcam_face_${Date.now()}.jpg`, { type: 'image/jpeg' });
      if (target === 'enroll') {
        setEnrollFile(file);
        setEnrollPreviewUrl(URL.createObjectURL(blob));
      } else {
        setProbeFile(file);
        setProbePreviewUrl(URL.createObjectURL(blob));
      }
      stopWebcam();
      playTacticalTone('verify');
    }, 'image/jpeg', 0.95);
  };

  useEffect(() => {
    return () => {
      if (webcamStreamRef.current) {
        webcamStreamRef.current.getTracks().forEach((t) => t.stop());
      }
    };
  }, []);

  const handleVerifyProbe = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!probeFile) return;
    setIsVerifyingProbe(true);
    playTacticalTone("click");
    setProbeResult(null);
    try {
      const res = await api.frsVerifyProbe(probeFile);
      setProbeResult(res);
      if (res.matched) {
        playTacticalTone("alert");
        setFeedback(`🚨 POSITIVE BIOMETRIC MATCH: Subject identified as ${res.subject_name} (${res.similarity_percent} similarity)!`);
        // Auto-open full interactive biometric match dossier modal!
        setSelectedMatch({
          subject_name: res.subject_name,
          similarity_percent: res.similarity_percent,
          similarity: res.similarity,
          threat_level: res.threat_level || "CATEGORY_A",
          notes: res.notes || "Enrolled in national border biometric lookout list.",
          photo_url: res.photo_url || (res.subject_id ? `/api/v1/watchlist/${res.subject_id}/image` : null),
          probe_url: probePreviewUrl || URL.createObjectURL(probeFile),
          legal_citation: res.legal_citation || "Bharatiya Sakshya Adhiniyam, 2023 — Section 63",
          timestamp: new Date().toLocaleTimeString(),
        });
      } else {
        playTacticalTone("verify");
        setFeedback(res.face_detected ? "✓ Face detected, but no matching identity in border watchlist gallery." : `⚠️ ${res.message}`);
      }
    } catch (err: any) {
      setFeedback(`Probe Verification Error: ${err.message}`);
    }
    setIsVerifyingProbe(false);
  };

  const loadData = () => {
    setLoading(true);
    Promise.all([
      api.watchlist().catch(() => []),
      api.watchlistMatches().catch(() => []),
    ])
      .then(([wsList, matchEvents]) => {
        setSuspects(wsList);
        setMatches(matchEvents);
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadData();
  }, []);

  const handleDelete = async (id: number) => {
    playTacticalTone("click");
    if (!confirm("Are you sure you want to remove this subject from the biometric watchlist?")) return;
    try {
      await api.deleteWatchlist(id);
      playTacticalTone("verify");
      setFeedback("✓ Subject removed from watchlist.");
      loadData();
    } catch (err: any) {
      setFeedback(`Delete error: ${err.message}`);
    }
  };

  const handleEnroll = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    if (!enrollFile) {
      alert("Please select or capture a face image for biometric enrollment.");
      return;
    }
    stopWebcam();
    try {
      await api.enrollWatchlist(name, notes, enrollFile);
      setShowEnroll(false);
      setName("");
      setNotes("");
      setEnrollFile(null);
      setEnrollPreviewUrl(null);
      playTacticalTone("verify");
      setFeedback(`✓ Suspect ${name} successfully enrolled into biometric watchlist.`);
      loadData();
    } catch (err: any) {
      setFeedback(`Enrollment error: ${err.message}`);
    }
  };

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Facial Recognition System (FRS) & Suspect Intelligence</h1>
          <p style={{ margin: 0, fontSize: 12, color: "var(--text-secondary)" }}>
            Deep learning biometric face matching, national border watchlist management, and operator verification studio
          </p>
        </div>
        <div style={{ display: "flex", gap: 10 }}>
          <button className="btn btn-secondary" onClick={loadData} disabled={loading}>
            {loading ? "Refreshing..." : "🔄 Refresh"}
          </button>
          <button className="btn btn-primary" onClick={() => setShowEnroll(true)}>
            + Enroll Suspect Biometrics
          </button>
        </div>
      </div>

      {feedback && (
        <div className={`test-feedback ${String(feedback).toLowerCase().includes("error") ? "fail" : "success"}`} style={{ marginBottom: 16 }}>
          {feedback}
        </div>
      )}

      {/* FRS KPI Ribbon */}
      <div className="tactical-kpi-ribbon" style={{ marginBottom: 16 }}>
        <div className="tactical-kpi-card">
          <div className="kpi-header-label">
            <span>BIOMETRIC GALLERY</span>
            <span style={{ color: "#00f0ff" }}>ENROLLED</span>
          </div>
          <div className="kpi-metric-val">{suspects.length}</div>
        </div>
        <div className="tactical-kpi-card">
          <div className="kpi-header-label">
            <span>ACTIVE EMBEDDINGS</span>
            <span style={{ color: "#00ff9d" }}>ARCFACE</span>
          </div>
          <div className="kpi-metric-val" style={{ color: "#00ff9d" }}>
            {suspects.filter((s) => s.has_embedding).length}
          </div>
        </div>
        <div className="tactical-kpi-card">
          <div className="kpi-header-label">
            <span>MATCHES RECORDED</span>
            <span style={{ color: "#ffaa00" }}>INCIDENTS</span>
          </div>
          <div className="kpi-metric-val" style={{ color: "#ffaa00" }}>
            {matches.length}
          </div>
        </div>
        <div className="tactical-kpi-card">
          <div className="kpi-header-label">
            <span>LEGAL CITATION</span>
            <span style={{ color: "#00f0ff" }}>STATUTE</span>
          </div>
          <div className="kpi-metric-val" style={{ fontSize: 13, color: "#00f0ff" }}>
            BSA 2023 §63
          </div>
        </div>
      </div>

      {/* Visual Guidance Banner: Where to upload photo & how matching works */}
      <div
        style={{
          background: "linear-gradient(90deg, rgba(0, 240, 255, 0.08) 0%, rgba(13, 27, 42, 0.6) 100%)",
          border: "1px solid rgba(0, 240, 255, 0.25)",
          borderRadius: 8,
          padding: "14px 18px",
          marginBottom: 16,
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 16,
          flexWrap: "wrap",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <div style={{ fontSize: 24 }}>💡</div>
          <div>
            <div style={{ fontSize: 13, fontWeight: 700, color: "#00f0ff", letterSpacing: "0.5px" }}>
              HOW TO USE FACE RECOGNITION (FRS) & PHOTO UPLOADS:
            </div>
            <div style={{ fontSize: 12, color: "#d1d5db", marginTop: 3, lineHeight: 1.5 }}>
              <b>1. Watchlist Enrollment (Kha upload krna hai):</b> Click <b>"+ Enroll Suspect"</b> (top right) to register a suspect face photo into the database with legal compliance metadata.
              <br />
              <b>2. Instant Verification (Probe Match):</b> Use <b>"Instant Probe Match"</b> (left panel) to test any suspect image against enrolled targets and immediately open the <b>Biometric Match Card</b>.
              <br />
              <b>3. Live Video Streaming:</b> When a person appears in front of the active camera, real-time AI draws tactical green/red bounding boxes directly on the video feed.
            </div>
          </div>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button
            className="btn btn-primary btn-sm"
            onClick={() => {
              playTacticalTone("click");
              setShowEnroll(true);
            }}
          >
            + Enroll New Face
          </button>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1.2fr 2fr", gap: 16 }}>
        {/* Left Column: Instant Probe Matching + Live Candidate Face Matches */}
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div className="panel" style={{ margin: 0, padding: 14 }}>
            <div className="panel-header" style={{ padding: "0 0 10px 0" }}>
              <b style={{ color: "#00f0ff", fontSize: 13 }}>🔍 Instant Probe Match (Field Interception)</b>
              <span className="panel-tag" style={{ borderColor: "#00f0ff", color: "#00f0ff" }}>SFace 128D</span>
            </div>
            <form onSubmit={handleVerifyProbe} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              <div>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
                  <label style={{ fontSize: 11, color: "var(--text-secondary)" }}>Suspect face source:</label>
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    style={{ padding: "2px 8px", fontSize: 10, borderColor: "#00f0ff", color: "#00f0ff" }}
                    onClick={() => {
                      if (webcamMode === "probe") stopWebcam();
                      else startWebcam("probe");
                    }}
                  >
                    {webcamMode === "probe" ? "✕ Close Camera" : "📸 Use Live Webcam"}
                  </button>
                </div>

                {webcamMode === "probe" ? (
                  <div style={{ position: "relative", borderRadius: 6, overflow: "hidden", background: "#000", border: "1px solid #00f0ff" }}>
                    <video ref={webcamVideoRef} autoPlay playsInline muted style={{ width: "100%", height: 180, objectFit: "cover" }} />
                    <div style={{ position: "absolute", top: 8, right: 8, background: "rgba(0,0,0,0.7)", padding: "2px 6px", borderRadius: 4, fontSize: 10, color: "#00ff9d" }}>
                      ● LIVE WEBCAM
                    </div>
                    <div style={{ position: "absolute", bottom: 8, left: "50%", transform: "translateX(-50%)" }}>
                      <button
                        type="button"
                        className="btn btn-primary btn-sm"
                        style={{ boxShadow: "0 0 12px rgba(0, 240, 255, 0.6)" }}
                        onClick={() => captureWebcamFrame("probe")}
                      >
                        📸 Snap Probe Photo
                      </button>
                    </div>
                  </div>
                ) : (
                  <input
                    type="file"
                    accept="image/*"
                    onChange={(e) => {
                      const f = e.target.files?.[0] || null;
                      setProbeFile(f);
                      if (f) setProbePreviewUrl(URL.createObjectURL(f));
                    }}
                    style={{ width: "100%", marginTop: 4, fontSize: 12, color: "#fff" }}
                  />
                )}

                {probePreviewUrl && (
                  <div style={{ marginTop: 6, display: "flex", alignItems: "center", gap: 8, background: "rgba(0,0,0,0.3)", padding: 6, borderRadius: 4 }}>
                    <img src={probePreviewUrl} alt="Probe Preview" style={{ width: 42, height: 42, objectFit: "cover", borderRadius: 4, border: "1px solid #00f0ff" }} />
                    <div style={{ fontSize: 11, color: "#94a3b8" }}>Probe image loaded & ready for SFace 128D extraction</div>
                  </div>
                )}
              </div>
              <button className="btn btn-primary btn-sm" type="submit" disabled={isVerifyingProbe || !probeFile}>
                {isVerifyingProbe ? "Computing Embedding & Matching..." : "⚡ Verify Probe Against Watchlist"}
              </button>
            </form>
            {probeResult && (
              <div style={{ marginTop: 10, padding: 12, borderRadius: 6, background: probeResult.matched ? "rgba(255, 42, 85, 0.12)" : "rgba(0, 255, 157, 0.08)", border: `1px solid ${probeResult.matched ? "#ff2a55" : "#00ff9d"}` }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <div style={{ fontSize: 13, fontWeight: 700, color: probeResult.matched ? "#ff2a55" : "#00ff9d" }}>
                    {probeResult.matched ? `🚨 POSITIVE MATCH: ${probeResult.subject_name}` : (probeResult.face_detected ? "✓ No Watchlist Match" : "⚠️ No Face Detected")}
                  </div>
                  {probeResult.matched && (
                    <button
                      className="btn btn-primary btn-sm"
                      style={{ padding: "3px 10px", fontSize: 11, background: "#ff2a55", borderColor: "#ff2a55" }}
                      onClick={() => {
                        playTacticalTone("click");
                        setSelectedMatch({
                          subject_name: probeResult.subject_name,
                          similarity_percent: probeResult.similarity_percent,
                          similarity: probeResult.similarity,
                          threat_level: probeResult.threat_level || "CATEGORY_A",
                          notes: probeResult.notes || "Enrolled in national border biometric lookout list.",
                          photo_url: probeResult.photo_url || (probeResult.subject_id ? `/api/v1/watchlist/${probeResult.subject_id}/image` : null),
                          probe_url: probePreviewUrl || (probeFile ? URL.createObjectURL(probeFile) : null),
                          legal_citation: probeResult.legal_citation || "Bharatiya Sakshya Adhiniyam, 2023 — Section 63",
                          timestamp: new Date().toLocaleTimeString(),
                        });
                      }}
                    >
                      🔍 Open Match Card
                    </button>
                  )}
                </div>
                {probeResult.matched && (
                  <div style={{ fontSize: 11, color: "var(--text-secondary)", marginTop: 6 }}>
                    Biometric Similarity: <b>{probeResult.similarity_percent}</b> • Classification: <b>{probeResult.threat_level}</b>
                  </div>
                )}
              </div>
            )}
          </div>

          <div className="panel" style={{ margin: 0 }}>
            <div className="panel-header">
              <b style={{ color: "#ff2a55", fontSize: 14 }}>🚨 Live Candidate Face Matches</b>
              <span className="panel-tag" style={{ borderColor: "#ff2a55", color: "#ff2a55" }}>
                INCIDENTS
              </span>
            </div>
            <div style={{ padding: 14, display: "flex", flexDirection: "column", gap: 14 }}>
              {matches.length === 0 ? (
                <Empty text="No candidate facial matches recorded yet. Run video analysis with face detection enabled or verify a probe above." />
              ) : (
              matches.map((m) => {
                const threat = m.threat_score || 85;
                return (
                  <div
                    key={m.id}
                    onClick={() => {
                      playTacticalTone("click");
                      setSelectedMatch({
                        subject_name: m.title.replace("Watchlist Match: ", "").replace("Match: ", ""),
                        similarity_percent: `${Math.round((m.confidence || 0.88) * 100)}%`,
                        similarity: m.confidence || 0.88,
                        threat_level: m.severity || "CRITICAL",
                        notes: m.description,
                        photo_url: null,
                        probe_url: null,
                        incident_id: m.id,
                        incident_code: m.incident_code,
                        legal_citation: "Bharatiya Sakshya Adhiniyam, 2023 — Section 63",
                        timestamp: new Date(m.created_at || Date.now()).toLocaleTimeString(),
                      });
                    }}
                    style={{
                      background: "rgba(255, 42, 85, 0.06)",
                      border: "1px solid rgba(255, 42, 85, 0.4)",
                      borderRadius: 6,
                      padding: 14,
                      display: "flex",
                      flexDirection: "column",
                      gap: 10,
                      cursor: "pointer",
                      transition: "border-color 0.2s, background 0.2s",
                    }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                      <b style={{ color: "#fff", fontSize: 13 }}>{m.title}</b>
                      <span className="sev-badge sev-critical">{m.severity}</span>
                    </div>

                    <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>
                      {m.description}
                    </div>

                    <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: "var(--text-ghost)" }}>
                      <span>Incident: {m.incident_code}</span>
                      <span>Threat Score: {threat}</span>
                    </div>

                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                      <span style={{ fontSize: 10, color: "#00f0ff" }}>Click card to view Biometric Dossier →</span>
                      {openInc && (
                        <button
                          className="btn btn-secondary btn-sm"
                          onClick={(e) => {
                            e.stopPropagation();
                            openInc(m.id);
                          }}
                        >
                          Inspect #{m.id}
                        </button>
                      )}
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>
      </div>

      {/* Suspect Watchlist Gallery */}
        <div className="panel" style={{ margin: 0 }}>
          <div className="panel-header">
            <b style={{ color: "#fff", fontSize: 14 }}>National Border Lookout & Biometric Watchlist</b>
            <span className="panel-tag">{suspects.length} ENROLLED SUBJECTS</span>
          </div>
          <div style={{ padding: 14 }}>
            {suspects.length === 0 ? (
              <Empty text="No data yet. Click '+ Enroll Suspect Biometrics' to add subjects to the watchlist." />
            ) : (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 14 }}>
                {suspects.map((s) => (
                  <div key={s.id} className="frs-suspect-card" style={{ background: "rgba(0,0,0,0.3)", borderRadius: 6, border: "1px solid rgba(255,255,255,0.08)", overflow: "hidden" }}>
                    <div style={{ height: 120, background: "#02060c", position: "relative", display: "flex", alignItems: "center", justifyContent: "center" }}>
                      {s.face_image_path && s.face_image_path !== "none" ? (
                        <img
                          src={`/api/v1/watchlist/${s.id}/image`}
                          alt={s.name}
                          style={{ width: "100%", height: "100%", objectFit: "cover" }}
                          onError={(e) => {
                            (e.target as HTMLElement).style.display = "none";
                          }}
                        />
                      ) : (
                        <span style={{ fontSize: 32 }}>👤</span>
                      )}
                      <span
                        style={{
                          position: "absolute",
                          top: 6,
                          right: 6,
                          background: s.has_embedding ? "#00ff9d" : "#ffaa00",
                          color: "#000",
                          fontWeight: 800,
                          fontSize: 9,
                          padding: "2px 6px",
                          borderRadius: 2,
                        }}
                      >
                        {s.has_embedding ? "EMBEDDED" : "PHOTO ONLY"}
                      </span>
                    </div>
                    <div style={{ padding: 10, display: "flex", flexDirection: "column", gap: 6 }}>
                      <b style={{ color: "#fff", fontSize: 13 }}>{s.name}</b>
                      {s.notes && (
                        <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>
                          {s.notes}
                        </div>
                      )}
                      <div style={{ fontSize: 9, color: "var(--text-ghost)" }}>
                        Enrolled: {new Date(s.created_at).toLocaleDateString()}
                      </div>
                      <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 4 }}>
                        <button
                          className="btn btn-sm btn-danger"
                          style={{ padding: "2px 8px", fontSize: 10 }}
                          onClick={() => handleDelete(s.id)}
                        >
                          🗑 Remove
                        </button>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Enroll Suspect Modal */}
      {showEnroll && (
        <div className="section-65b-modal-backdrop" onClick={() => { stopWebcam(); setShowEnroll(false); }}>
          <div className="panel" style={{ maxWidth: 480, width: "100%", margin: 0, padding: 24 }} onClick={(e) => e.stopPropagation()}>
            <h3 style={{ color: "#00f0ff", marginBottom: 14 }}>Enroll Subject into Biometric Watchlist</h3>
            <form onSubmit={handleEnroll} style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <div>
                <label style={{ fontSize: 11, color: "var(--text-secondary)" }}>Full Name / Identification Code:</label>
                <input
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="e.g. Tariq Mehmood"
                  data-testid="frs-enroll-input"
                  style={{ width: "100%", background: "#040b14", border: "1px solid #333", color: "#fff", padding: 8, borderRadius: 4, marginTop: 4 }}
                  required
                />
              </div>
              <div>
                <label style={{ fontSize: 11, color: "var(--text-secondary)" }}>Tactical Notes / Risk Profile:</label>
                <input
                  type="text"
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  placeholder="e.g. Suspected cross-border infiltration / contraband trafficking"
                  style={{ width: "100%", background: "#040b14", border: "1px solid #333", color: "#fff", padding: 8, borderRadius: 4, marginTop: 4 }}
                />
              </div>
              <div>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
                  <label style={{ fontSize: 11, color: "var(--text-secondary)" }}>Face Photo (JPG / PNG):</label>
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    style={{ padding: "2px 8px", fontSize: 10, borderColor: "#00f0ff", color: "#00f0ff" }}
                    onClick={() => {
                      if (webcamMode === "enroll") stopWebcam();
                      else startWebcam("enroll");
                    }}
                  >
                    {webcamMode === "enroll" ? "✕ Close Camera" : "📸 Capture from Webcam"}
                  </button>
                </div>

                {webcamMode === "enroll" ? (
                  <div style={{ position: "relative", borderRadius: 6, overflow: "hidden", background: "#000", border: "1px solid #00f0ff", marginBottom: 8 }}>
                    <video ref={webcamVideoRef} autoPlay playsInline muted style={{ width: "100%", height: 200, objectFit: "cover" }} />
                    <div style={{ position: "absolute", top: 8, right: 8, background: "rgba(0,0,0,0.7)", padding: "2px 6px", borderRadius: 4, fontSize: 10, color: "#00ff9d" }}>
                      ● LIVE WEBCAM
                    </div>
                    <div style={{ position: "absolute", bottom: 8, left: "50%", transform: "translateX(-50%)" }}>
                      <button
                        type="button"
                        className="btn btn-primary btn-sm"
                        style={{ boxShadow: "0 0 12px rgba(0, 240, 255, 0.6)" }}
                        onClick={() => captureWebcamFrame("enroll")}
                      >
                        📸 Snap & Use Photo
                      </button>
                    </div>
                  </div>
                ) : (
                  <input
                    type="file"
                    accept="image/*"
                    onChange={(e) => {
                      const f = e.target.files?.[0] || null;
                      setEnrollFile(f);
                      if (f) setEnrollPreviewUrl(URL.createObjectURL(f));
                    }}
                    style={{ width: "100%", background: "#040b14", border: "1px solid #333", color: "#fff", padding: 8, borderRadius: 4 }}
                  />
                )}

                {enrollPreviewUrl && (
                  <div style={{ marginTop: 6, display: "flex", alignItems: "center", gap: 8, background: "rgba(0,0,0,0.3)", padding: 6, borderRadius: 4 }}>
                    <img src={enrollPreviewUrl} alt="Enroll Preview" style={{ width: 42, height: 42, objectFit: "cover", borderRadius: 4, border: "1px solid #00f0ff" }} />
                    <div style={{ fontSize: 11, color: "#94a3b8" }}>Photo selected: {enrollFile?.name || "Webcam snapshot"} (Ready for SFace 128D embedding)</div>
                  </div>
                )}
              </div>
              <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 8 }}>
                <button className="btn btn-secondary" type="button" onClick={() => { stopWebcam(); setShowEnroll(false); }}>
                  Cancel
                </button>
                <button className="btn btn-primary" type="submit" data-testid="frs-enroll-submit" disabled={!enrollFile || !name.trim()}>
                  + Enroll Biometric Target
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
      {/* Interactive Biometric Match Dossier Modal */}
      {selectedMatch && (
        <div className="section-65b-modal-backdrop" onClick={() => setSelectedMatch(null)}>
          <div
            className="panel"
            style={{
              maxWidth: 620,
              width: "100%",
              margin: 0,
              padding: 24,
              border: "1px solid #ff2a55",
              boxShadow: "0 0 35px rgba(255, 42, 85, 0.35)",
              background: "#080e18",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16, borderBottom: "1px solid rgba(255, 42, 85, 0.3)", paddingBottom: 10 }}>
              <div>
                <b style={{ color: "#ff2a55", fontSize: 15, letterSpacing: "1px" }}>
                  🚨 BIOMETRIC MATCH DOSSIER
                </b>
                <div style={{ fontSize: 10, color: "var(--text-ghost)", marginTop: 2 }}>
                  BSA 2023 §63 Compliant • Automated Watchlist Interception
                </div>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span
                  style={{
                    background: "#ff2a55",
                    color: "#fff",
                    fontWeight: 800,
                    fontSize: 11,
                    padding: "3px 8px",
                    borderRadius: 4,
                  }}
                >
                  {selectedMatch.similarity_percent || "POSITIVE MATCH"}
                </span>
                <button
                  className="btn btn-secondary btn-sm"
                  style={{ padding: "2px 8px", fontSize: 12 }}
                  onClick={() => setSelectedMatch(null)}
                >
                  ✕
                </button>
              </div>
            </div>

            {/* Visual Side-by-Side Verification Cards */}
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14, marginBottom: 16 }}>
              {/* Probe / Intercepted Image */}
              <div style={{ background: "rgba(0,0,0,0.4)", borderRadius: 6, border: "1px solid rgba(255,255,255,0.1)", padding: 10, textAlign: "center" }}>
                <div style={{ fontSize: 10, color: "#00f0ff", fontWeight: 700, marginBottom: 6 }}>
                  INTERCEPTED PROBE / STREAM
                </div>
                <div style={{ height: 140, background: "#02060c", borderRadius: 4, overflow: "hidden", display: "flex", alignItems: "center", justifyContent: "center" }}>
                  {selectedMatch.probe_url ? (
                    <img src={selectedMatch.probe_url} alt="Probe" style={{ width: "100%", height: "100%", objectFit: "cover" }} />
                  ) : (
                    <span style={{ fontSize: 40 }}>🎯</span>
                  )}
                </div>
                <div style={{ fontSize: 10, color: "var(--text-secondary)", marginTop: 6 }}>
                  Source: Live Camera / Intercept Probe
                </div>
              </div>

              {/* Master Watchlist Enrolled Image */}
              <div style={{ background: "rgba(0,0,0,0.4)", borderRadius: 6, border: "1px solid rgba(255,42,85,0.3)", padding: 10, textAlign: "center" }}>
                <div style={{ fontSize: 10, color: "#ff2a55", fontWeight: 700, marginBottom: 6 }}>
                  ENROLLED WATCHLIST MASTER
                </div>
                <div style={{ height: 140, background: "#02060c", borderRadius: 4, overflow: "hidden", display: "flex", alignItems: "center", justifyContent: "center" }}>
                  {selectedMatch.photo_url ? (
                    <img
                      src={selectedMatch.photo_url}
                      alt={selectedMatch.subject_name}
                      style={{ width: "100%", height: "100%", objectFit: "cover" }}
                      onError={(e) => {
                        (e.target as HTMLElement).style.display = "none";
                      }}
                    />
                  ) : (
                    <span style={{ fontSize: 40 }}>👤</span>
                  )}
                </div>
                <div style={{ fontSize: 10, color: "var(--text-secondary)", marginTop: 6 }}>
                  Gallery Record: {selectedMatch.subject_name}
                </div>
              </div>
            </div>

            {/* Target Profile Dossier Details */}
            <div style={{ display: "flex", flexDirection: "column", gap: 8, background: "rgba(0,0,0,0.3)", padding: 12, borderRadius: 6, border: "1px solid rgba(255,255,255,0.06)", fontSize: 12 }}>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "var(--text-secondary)" }}>Identified Subject:</span>
                <b style={{ color: "#fff", fontSize: 14 }}>{selectedMatch.subject_name}</b>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "var(--text-secondary)" }}>Biometric Match Confidence:</span>
                <b style={{ color: "#00ff9d" }}>{selectedMatch.similarity_percent || "Match Confirmed"} (Cosine SFace)</b>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "var(--text-secondary)" }}>Threat Classification:</span>
                <span className="sev-badge sev-critical">{selectedMatch.threat_level || "CRITICAL"}</span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "var(--text-secondary)" }}>Detection Timestamp:</span>
                <span style={{ color: "#00f0ff" }}>{selectedMatch.timestamp || new Date().toLocaleTimeString()}</span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "var(--text-secondary)" }}>Statutory Evidentiary Basis:</span>
                <span style={{ color: "#ffaa00" }}>{selectedMatch.legal_citation || "Bharatiya Sakshya Adhiniyam, 2023 §63"}</span>
              </div>
              {selectedMatch.notes && (
                <div style={{ marginTop: 4, borderTop: "1px solid rgba(255,255,255,0.06)", paddingTop: 6 }}>
                  <div style={{ fontSize: 10, color: "var(--text-secondary)", marginBottom: 2 }}>TACTICAL INTELLIGENCE NOTES:</div>
                  <div style={{ color: "#e2e8f0", fontSize: 11, lineHeight: 1.4 }}>{selectedMatch.notes}</div>
                </div>
              )}
            </div>

            {/* Modal Bottom Actions */}
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 16 }}>
              {selectedMatch.incident_id && openInc ? (
                <button
                  className="btn btn-primary btn-sm"
                  onClick={() => {
                    const incId = selectedMatch.incident_id;
                    setSelectedMatch(null);
                    openInc(incId);
                  }}
                >
                  🔍 View Full Incident #{selectedMatch.incident_id} Evidence
                </button>
              ) : (
                <div style={{ fontSize: 11, color: "var(--text-ghost)" }}>
                  Verified by IBVAP Neural Face Recognition Core
                </div>
              )}
              <button className="btn btn-secondary btn-sm" onClick={() => setSelectedMatch(null)}>
                Close Dossier
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
