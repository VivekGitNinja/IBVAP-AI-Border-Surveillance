import React, { useState, useEffect, useRef } from "react";
import { api } from "../api";
import type { MediaAsset, AnalysisJob, Incident } from "../types";
import { playTacticalTone } from "../utils/audio";
import { TacticalHUD } from "../components/TacticalHUD";
import { ENABLE_SENSOR_SKINS } from "../config";

export function MediaAnalysisView() {
  // State
  const [assets, setAssets] = useState<MediaAsset[]>([]);
  const [jobs, setJobs] = useState<AnalysisJob[]>([]);
  const [zones, setZones] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploadSuccess, setUploadSuccess] = useState<string | null>(null);

  // Analysis launch modal state
  const [selectedAssetForJob, setSelectedAssetForJob] = useState<MediaAsset | null>(null);
  const [detectorModel, setDetectorModel] = useState<string>("yolo11n");
  const [confidenceThreshold, setConfidenceThreshold] = useState<number>(0.35);
  const [selectedZoneIds, setSelectedZoneIds] = useState<number[]>([]);
  const [enableTracking, setEnableTracking] = useState(true);
  const [enableAnpr, setEnableAnpr] = useState(true);
  const [enableBehavior, setEnableBehavior] = useState(true);
  const [enableNightMode, setEnableNightMode] = useState(true);
  const [enableFace, setEnableFace] = useState(false);

  // Active / selected job
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [activeJob, setActiveJob] = useState<AnalysisJob | null>(null);
  const [activeResults, setActiveResults] = useState<{
    job: AnalysisJob;
    detections: any[];
    incidents: Incident[];
    evidence: any[];
  } | null>(null);

  // Track Trails & Active Roster State (Tasks 2 & 3)
  const [jobTracks, setJobTracks] = useState<any[]>([]);
  const [selectedTrackId, setSelectedTrackId] = useState<string | null>(null);
  const [sensorSkin, setSensorSkin] = useState<"raw" | "nvg" | "flir">("raw");
  const videoPlayerRef = useRef<HTMLVideoElement | null>(null);
  const trailCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const animFrameIdRef = useRef<number | null>(null);

  const startPlaybackSync = () => {
    if (animFrameIdRef.current) cancelAnimationFrame(animFrameIdRef.current);
    const renderLoop = () => {
      const vid = videoPlayerRef.current;
      if (vid && !vid.paused && !vid.ended) {
        drawTrailsOnCanvas(vid.currentTime);
        animFrameIdRef.current = requestAnimationFrame(renderLoop);
      }
    };
    animFrameIdRef.current = requestAnimationFrame(renderLoop);
  };

  const stopPlaybackSync = () => {
    if (animFrameIdRef.current) {
      cancelAnimationFrame(animFrameIdRef.current);
      animFrameIdRef.current = null;
    }
    if (videoPlayerRef.current) {
      drawTrailsOnCanvas(videoPlayerRef.current.currentTime);
    }
  };

  useEffect(() => {
    return () => {
      if (animFrameIdRef.current) cancelAnimationFrame(animFrameIdRef.current);
    };
  }, []);

  // Evidence verification state
  const [evidenceVerifyStatus, setEvidenceVerifyStatus] = useState<Record<number, any>>({});

  // Detection Feed Filters
  const [filterClass, setFilterClass] = useState<string>("");
  const [filterNightOnly, setFilterNightOnly] = useState<boolean>(false);
  const [filterZone, setFilterZone] = useState<string>("");

  // Virtual Fence Studio (Task 5.1)
  const [showZoneEditor, setShowZoneEditor] = useState<boolean>(false);
  const [zoneDrawMode, setZoneDrawMode] = useState<"line" | "polygon">("line");
  const [zonePoints, setZonePoints] = useState<[number, number][]>([]);
  const [zoneName, setZoneName] = useState<string>("");
  const [zoneDirection, setZoneDirection] = useState<string>("either");
  const [zoneNightOnly, setZoneNightOnly] = useState<boolean>(false);
  const [zoneMinConfidence, setZoneMinConfidence] = useState<number>(0.3);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  // Preview modal
  const [previewMedia, setPreviewMedia] = useState<MediaAsset | null>(null);

  // WebSocket for active job
  const wsRef = useRef<WebSocket | null>(null);
  const [wsConnected, setWsConnected] = useState(false);

  // File input ref
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [dragOver, setDragOver] = useState(false);

  // Load initial media, jobs, and zones
  const refreshData = async () => {
    setLoading(true);
    try {
      const [mediaList, jobList, zoneList] = await Promise.all([
        api.mediaAssets().catch(() => []),
        api.analysisJobs().catch(() => []),
        api.zones().catch(() => []),
      ]);
      setAssets(mediaList);
      setJobs(jobList);
      setZones(zoneList);
      if (jobList.length > 0 && !activeJobId) {
        setActiveJobId(String(jobList[0].id || jobList[0].job_id));
      }
    } catch (e: any) {
      console.error("Failed to load media or jobs:", e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refreshData();
  }, []);

  // Poll jobs list if any job is pending or running
  useEffect(() => {
    const hasRunning = jobs.some((j: any) => j.status === "queued" || j.status === "processing" || j.status === "RUNNING");
    if (!hasRunning) return;

    const interval = setInterval(async () => {
      try {
        const updatedJobs = await api.analysisJobs();
        setJobs(updatedJobs);
        if (activeJobId) {
          const current = updatedJobs.find((j: any) => String(j.id || j.job_id) === String(activeJobId));
          if (current) {
            setActiveJob(current);
            const jid = parseInt(String(activeJobId), 10);
            api.analysisResults(jid).then(setActiveResults).catch(() => {});
            api.analysisJobTracks(jid).then((res: any) => setJobTracks(res.tracks || [])).catch(() => {});
          }
        }
      } catch {}
    }, 2000);

    return () => clearInterval(interval);
  }, [jobs, activeJobId]);

  // Handle active job selection and results fetching
  useEffect(() => {
    if (!activeJobId) {
      setActiveJob(null);
      setActiveResults(null);
      return;
    }

    const jid = parseInt(activeJobId, 10);
    api.analysisResults(jid)
      .then((res: any) => {
        setActiveJob(res);
        setActiveResults(res);
      })
      .catch((err) => console.error("Failed to fetch job results:", err));

    api.analysisJobTracks(jid)
      .then((res: any) => {
        setJobTracks(res.tracks || []);
      })
      .catch(() => setJobTracks([]));

    // Connect WebSocket
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = window.location.host;
    const token = typeof localStorage !== "undefined" ? (localStorage.getItem("token") || localStorage.getItem("ibvap_token") || "") : "";
    const wsUrl = `${proto}//${host}/ws/analysis/${activeJobId}?token=${encodeURIComponent(token)}`;

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => setWsConnected(true);
    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.event === "job_progress") {
          setActiveJob((prev: any) => ({
            ...prev,
            progress_percent: data.progress_percent,
            processed_frames: data.processed_frames,
            detections_count: data.detections_count,
            incidents_count: data.incidents_count,
            fps: data.fps,
            status: data.status,
          }));
        } else if (data.event === "incident_created") {
          playTacticalTone("alert");
          setActiveResults((prev: any) => {
            if (!prev) return prev;
            return {
              ...prev,
              incidents: [data.incident, ...(prev.incidents || [])],
            };
          });
        } else if (data.event === "detection_created") {
          if (data.detection) {
            setActiveResults((prev: any) => {
              if (!prev) return { detections: [data.detection] };
              return {
                ...prev,
                detections: [data.detection, ...(prev.detections || [])],
              };
            });
          }
          const tid = data.detection?.track_id;
          if (tid) {
            setJobTracks((prev) => {
              if (prev.some((t) => t.track_id === tid)) return prev;
              return [
                ...prev,
                {
                  track_id: tid,
                  class: data.detection.label || "unknown",
                  first_frame: data.detection.frame_index || 0,
                  last_frame: data.detection.frame_index || 0,
                  path: [[0.5, 0.5, data.detection.frame_index || 0]],
                  points_count: 1,
                  max_speed: 0.0,
                  zones_touched: [],
                  night_frame_pct: data.detection.night ? 100.0 : 0.0,
                  detections_count: 1,
                },
              ];
            });
          }
        } else if (data.event === "job_completed") {
          playTacticalTone("verify");
          api.analysisResults(jid).then((res: any) => {
            setActiveResults(res);
            setActiveJob(res);
          });
          api.analysisJobTracks(jid).then((res: any) => setJobTracks(res.tracks || []));
        }
      } catch (err) {
        console.error("WS parse error:", err);
      }
    };
    ws.onclose = () => setWsConnected(false);

    return () => {
      ws.close();
    };
  }, [activeJobId]);

  // Track polyline & detection bounding box canvas renderer
  const drawTrailsOnCanvas = (currentTimeSec: number) => {
    const canvas = trailCanvasRef.current;
    const video = videoPlayerRef.current;
    if (!canvas || !video) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    if (canvas.width !== video.clientWidth || canvas.height !== video.clientHeight) {
      canvas.width = video.clientWidth || 640;
      canvas.height = video.clientHeight || 360;
    }

    ctx.clearRect(0, 0, canvas.width, canvas.height);

    const fps = activeJob?.fps || 15.0;
    const currentFrame = Math.floor(currentTimeSec * fps);

    // 1. Draw persistent track trails
    jobTracks.forEach((tr) => {
      const isSelected = selectedTrackId === tr.track_id;
      if (!isSelected && selectedTrackId !== null) return;

      const points = tr.path || [];
      if (points.length < 2) return;

      const activePts = isSelected ? points : points.filter((p: any) => p[2] <= currentFrame + 5);
      if (activePts.length < 2) return;

      ctx.beginPath();
      ctx.lineWidth = isSelected ? 3 : 1.5;
      ctx.strokeStyle = isSelected ? "#00f0ff" : "rgba(0, 240, 255, 0.4)";
      if (isSelected) {
        ctx.shadowColor = "#00f0ff";
        ctx.shadowBlur = 8;
      } else {
        ctx.shadowColor = "transparent";
        ctx.shadowBlur = 0;
      }

      activePts.forEach((pt: any, idx: number) => {
        const px = pt[0] * canvas.width;
        const py = pt[1] * canvas.height;
        if (idx === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      });
      ctx.stroke();

      // Current head marker
      const head = activePts[activePts.length - 1];
      const hx = head[0] * canvas.width;
      const hy = head[1] * canvas.height;

      ctx.beginPath();
      ctx.arc(hx, hy, isSelected ? 5 : 3.5, 0, Math.PI * 2);
      ctx.fillStyle = isSelected ? "#00ff9d" : "#00f0ff";
      ctx.fill();

      // Label
      ctx.font = "bold 9px monospace";
      ctx.fillStyle = isSelected ? "#00ff9d" : "#94a3b8";
      ctx.fillText(tr.track_id, hx + 6, hy + 3);
    });

    // 2. Draw current-frame detection bounding boxes & plate chips
    const allDets = activeResults?.detections || [];
    if (allDets.length > 0) {
      const vWidth = video.videoWidth || canvas.width || 640;
      const vHeight = video.videoHeight || canvas.height || 360;

      // Filter detections belonging to current frame (within +/- 2 frame window for seamless multi-stride playback)
      const frameDets = allDets.filter((d: any) => {
        const fIdx = d.frame ?? d.frame_index ?? 0;
        if (selectedTrackId && String(d.track_id) !== String(selectedTrackId)) return false;
        return Math.abs(fIdx - currentFrame) <= 2;
      });

      frameDets.forEach((det: any) => {
        const rawBbox = det.bbox || [det.bbox_x1, det.bbox_y1, det.bbox_x2, det.bbox_y2];
        if (!rawBbox || rawBbox.length !== 4) return;

        let [x1, y1, x2, y2] = rawBbox;
        let bx: number, by: number, bw: number, bh: number;

        if (x1 <= 1.0 && x2 <= 1.0 && y1 <= 1.0 && y2 <= 1.0) {
          bx = x1 * canvas.width;
          by = y1 * canvas.height;
          bw = (x2 - x1) * canvas.width;
          bh = (y2 - y1) * canvas.height;
        } else {
          const scaleX = canvas.width / vWidth;
          const scaleY = canvas.height / vHeight;
          bx = x1 * scaleX;
          by = y1 * scaleY;
          bw = (x2 - x1) * scaleX;
          bh = (y2 - y1) * scaleY;
        }

        if (bw <= 0 || bh <= 0) return;

        const label = (det.label || "OBJECT").toLowerCase();
        const conf = Number(det.confidence || 0.85);
        const tid = det.track_id ? `TRK-${det.track_id}` : "";
        const payload = det.payload || det.metadata || {};
        const plateText = det.plate_text || payload.plate_text;

        let boxColor = "#00f0ff";
        if (label === "person") {
          boxColor = "#00ff9d";
        } else if (["car", "truck", "bus", "motorcycle", "vehicle"].includes(label)) {
          boxColor = plateText ? "#00f0ff" : "#ffaa00";
        } else if (label === "face") {
          boxColor = "#00f0ff";
        }

        // Draw bounding box
        ctx.save();
        ctx.strokeStyle = boxColor;
        ctx.lineWidth = 2;
        ctx.strokeRect(bx, by, bw, bh);

        // Corner accents
        const cLen = Math.min(10, bw / 3, bh / 3);
        ctx.lineWidth = 3;
        ctx.beginPath();
        ctx.moveTo(bx, by + cLen); ctx.lineTo(bx, by); ctx.lineTo(bx + cLen, by);
        ctx.moveTo(bx + bw - cLen, by); ctx.lineTo(bx + bw, by); ctx.lineTo(bx + bw, by + cLen);
        ctx.moveTo(bx, by + bh - cLen); ctx.lineTo(bx, by + bh); ctx.lineTo(bx + cLen, by + bh);
        ctx.moveTo(bx + bw - cLen, by + bh); ctx.lineTo(bx + bw, by + bh); ctx.lineTo(bx + bw, by + bh - cLen);
        ctx.stroke();

        // Label pill with collision avoidance and edge clamping
        const tagText = `${(label || "OBJECT").toUpperCase()} ${tid ? `[${tid}] ` : ""}${((conf || 0) * 100).toFixed(0)}%`;
        ctx.font = "bold 11px monospace";
        const tagWidth = ctx.measureText(tagText).width;
        const pillHeight = 20;
        const pillY = by >= pillHeight + 2 ? by - pillHeight : by + bh;
        const pillX = Math.max(2, Math.min(bx, canvas.width - tagWidth - 14));

        ctx.fillStyle = "rgba(4, 11, 20, 0.92)";
        ctx.fillRect(pillX, pillY, tagWidth + 12, pillHeight);
        ctx.strokeStyle = boxColor;
        ctx.lineWidth = 1;
        ctx.strokeRect(pillX, pillY, tagWidth + 12, pillHeight);
        ctx.fillStyle = boxColor;
        ctx.fillText(tagText, pillX + 6, pillY + 14);

        // License plate badge
        if (plateText) {
          const pText = `🚗 IND ${plateText}`;
          ctx.font = "bold 11px monospace";
          const pWidth = ctx.measureText(pText).width;
          const py = by + bh + 4;
          const px = Math.max(2, Math.min(bx, canvas.width - pWidth - 16));
          ctx.fillStyle = "rgba(4, 11, 20, 0.94)";
          ctx.fillRect(px, py, pWidth + 12, 20);
          ctx.strokeStyle = "#00ff9d";
          ctx.lineWidth = 1;
          ctx.strokeRect(px, py, pWidth + 12, 20);
          ctx.fillStyle = "#ffffff";
          ctx.fillText(pText, px + 6, py + 14);
        }
        ctx.restore();
      });
    }
  };

  const handleSelectTrack = (tid: string) => {
    playTacticalTone("click");
    if (selectedTrackId === tid) {
      setSelectedTrackId(null);
      drawTrailsOnCanvas(videoPlayerRef.current?.currentTime || 0);
      return;
    }
    setSelectedTrackId(tid);
    const trk = jobTracks.find((t) => t.track_id === tid);
    if (trk && videoPlayerRef.current) {
      const fps = activeJob?.fps || 15.0;
      videoPlayerRef.current.currentTime = trk.first_frame / fps;
      setTimeout(() => drawTrailsOnCanvas(trk.first_frame / fps), 100);
    }
  };

  // Redraw canvas points for Zone Studio
  useEffect(() => {
    if (!showZoneEditor) return;
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    ctx.clearRect(0, 0, canvas.width, canvas.height);
    // Dark tactical grid background
    ctx.fillStyle = "#040914";
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    // Draw grid
    ctx.strokeStyle = "rgba(0, 240, 255, 0.1)";
    ctx.lineWidth = 1;
    for (let x = 0; x < canvas.width; x += 40) {
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, canvas.height);
      ctx.stroke();
    }
    for (let y = 0; y < canvas.height; y += 40) {
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(canvas.width, y);
      ctx.stroke();
    }

    if (zonePoints.length === 0) return;

    // Draw lines / polygon
    ctx.strokeStyle = zoneDrawMode === "line" ? "#00f0ff" : "#ffaa00";
    ctx.lineWidth = 2;
    ctx.beginPath();
    const [p0x, p0y] = zonePoints[0];
    ctx.moveTo(p0x * canvas.width, p0y * canvas.height);

    for (let i = 1; i < zonePoints.length; i++) {
      const [px, py] = zonePoints[i];
      ctx.lineTo(px * canvas.width, py * canvas.height);
    }

    if (zoneDrawMode === "polygon" && zonePoints.length > 2) {
      ctx.closePath();
      ctx.fillStyle = "rgba(255, 170, 0, 0.2)";
      ctx.fill();
    }
    ctx.stroke();

    // Draw vertex handles
    zonePoints.forEach(([px, py], idx) => {
      ctx.fillStyle = idx === 0 ? "#00ff9d" : "#ff2a55";
      ctx.beginPath();
      ctx.arc(px * canvas.width, py * canvas.height, 5, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = "#ffffff";
      ctx.font = "10px monospace";
      ctx.fillText(String.fromCharCode(65 + idx), px * canvas.width + 7, py * canvas.height - 4);
    });
  }, [showZoneEditor, zonePoints, zoneDrawMode]);

  const handleCanvasClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const x = (e.clientX - rect.left) / rect.width;
    const y = (e.clientY - rect.top) / rect.height;

    const normX = Math.max(0.0, Math.min(1.0, parseFloat(x.toFixed(4))));
    const normY = Math.max(0.0, Math.min(1.0, parseFloat(y.toFixed(4))));

    if (zoneDrawMode === "line" && zonePoints.length >= 2) {
      // Line mode supports 2 points
      setZonePoints([[normX, normY]]);
    } else {
      setZonePoints((prev) => [...prev, [normX, normY]]);
    }
  };

  const handleSaveZone = async () => {
    if (!zoneName.trim()) {
      alert("Please provide a name for this virtual fence zone");
      return;
    }
    if (zoneDrawMode === "line" && zonePoints.length < 2) {
      alert("A tripwire line requires exactly 2 endpoints (Point A and Point B)");
      return;
    }
    if (zoneDrawMode === "polygon" && zonePoints.length < 3) {
      alert("A perimeter polygon requires at least 3 vertices");
      return;
    }

    try {
      await api.createZone({
        name: zoneName.trim(),
        zone_type: zoneDrawMode,
        geometry: {
          type: zoneDrawMode,
          points: zonePoints,
        },
        direction: zoneDirection,
        night_only: zoneNightOnly,
        min_confidence: zoneMinConfidence,
      });
      playTacticalTone("verify");
      setZoneName("");
      setZonePoints([]);
      await refreshData();
    } catch (err: any) {
      alert(`Failed to save zone: ${err.message}`);
    }
  };

  const handleDeleteZone = async (id: number) => {
    if (!confirm("Remove this virtual fence zone?")) return;
    try {
      await api.deleteZone(id);
      refreshData();
    } catch (err: any) {
      alert(`Delete error: ${err.message}`);
    }
  };

  // Upload handler
  const handleUpload = async (file: File) => {
    setUploadError(null);
    setUploadSuccess(null);
    setUploadProgress(0);
    playTacticalTone("click");

    try {
      const asset = await api.uploadMedia(file);
      playTacticalTone("verify");
      setUploadSuccess(`Video ingested: ${asset.original_filename} (${asset.width}x${asset.height} @ ${asset.fps?.toFixed(1) || 25} FPS)`);
      setUploadProgress(null);
      await refreshData();
      setSelectedAssetForJob(asset);
    } catch (err: any) {
      playTacticalTone("alert");
      setUploadError(err.message || "Failed to upload video asset");
      setUploadProgress(null);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      handleUpload(e.dataTransfer.files[0]);
    }
  };

  // Launch job
  const handleLaunchAnalysis = async () => {
    if (!selectedAssetForJob) return;
    playTacticalTone("click");
    try {
      const job = await api.createAnalysisJob({
        source_type: "upload",
        source_id: selectedAssetForJob.id,
        detector_model: detectorModel,
        confidence_threshold: confidenceThreshold,
        zone_ids: selectedZoneIds,
        enable_anpr: enableAnpr,
        enable_tracking: enableTracking,
        enable_behavior: enableBehavior,
        enable_night_mode: enableNightMode,
        enable_face: enableFace,
      });
      playTacticalTone("verify");
      setSelectedAssetForJob(null);
      await refreshData();
      setActiveJobId(String(job.id || job.job_id));
    } catch (err: any) {
      alert(`Launch error: ${err.message}`);
    }
  };

  // Verify evidence hash
  const handleVerifyEvidence = async (id: number) => {
    try {
      const res = await api.verifyEvidenceHash(id);
      playTacticalTone(res.match ? "verify" : "alert");
      setEvidenceVerifyStatus((prev) => ({ ...prev, [id]: res }));
    } catch (err: any) {
      alert(`Hash verify error: ${err.message}`);
    }
  };

  // Filtered detections
  const rawDetections = activeResults?.detections || [];
  const filteredDetections = rawDetections.filter((det: any) => {
    if (selectedTrackId && String(det.track_id) !== String(selectedTrackId)) return false;
    if (filterClass) {
      const l = (det.label || "").toLowerCase();
      if (filterClass === "person" && l !== "person") return false;
      if (filterClass === "vehicle" && !["car", "truck", "bus", "motorcycle", "vehicle"].includes(l)) return false;
      if (filterClass === "motion" && l !== "motion") return false;
    }
    if (filterNightOnly && !det.payload?.night) return false;
    return true;
  });

  return (
    <div className="page">
      {/* Header */}
      <div className="page-header">
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <h1 style={{ margin: 0 }}>Tactical Video Studio & Edge CV Pipeline</h1>
            <span
              className="page-tag"
              style={{
                background: "rgba(0, 255, 157, 0.15)",
                borderColor: "#00ff9d",
                color: "#00ff9d",
                fontWeight: "bold",
              }}
            >
              ● REAL ANALYSIS PIPELINE ACTIVE
            </span>
          </div>
          <p style={{ margin: "4px 0 0", fontSize: 12, color: "var(--text-secondary)" }}>
            Real surveillance footage, virtual fence tripwires & polygons, multi-object tracking, and BSA 2023 §63 forensic reports
          </p>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <button className="btn btn-secondary" onClick={() => setShowZoneEditor(true)}>
            📐 Virtual Fence Studio ({zones.length} Zones)
          </button>
          <button className="btn btn-secondary" onClick={refreshData} disabled={loading}>
            {loading ? "Refreshing..." : "🔄 Refresh Studio"}
          </button>
          <button className="btn btn-primary" onClick={() => fileInputRef.current?.click()} data-testid="upload-button">
            ⬆ Upload Video
          </button>
          <input
            type="file"
            ref={fileInputRef}
            data-testid="upload-input"
            style={{ display: "none" }}
            accept=".mp4,.mov,.avi,.mkv,.webm"
            onChange={(e) => {
              if (e.target.files && e.target.files[0]) {
                handleUpload(e.target.files[0]);
              }
            }}
          />
        </div>
      </div>

      {/* Upload Zone & Alerts */}
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
        style={{
          border: dragOver ? "2px dashed #00f0ff" : "1px dashed rgba(0, 240, 255, 0.3)",
          borderRadius: 8,
          background: dragOver ? "rgba(0, 240, 255, 0.08)" : "rgba(10, 15, 25, 0.5)",
          padding: "16px",
          textAlign: "center",
          marginBottom: 16,
          cursor: "pointer",
        }}
        onClick={() => fileInputRef.current?.click()}
      >
        <div style={{ fontSize: 24, marginBottom: 4 }}>📹 ⬆ 📁</div>
        <div style={{ color: "#fff", fontWeight: 600, fontSize: 13 }}>
          Drag & Drop Real Video Files Here or Click to Browse
        </div>
        <div style={{ fontSize: 11, color: "var(--text-secondary)", marginTop: 2 }}>
          MP4, MOV, AVI, MKV, WEBM • SHA-256 Hashed on Ingestion • No Demo Data Presented as Real
        </div>

        {/* 1-Click Evaluation Fixtures Banner */}
        <div style={{ marginTop: 12, paddingTop: 10, borderTop: "1px solid rgba(255,255,255,0.06)", display: "flex", alignItems: "center", justifyContent: "center", gap: 10, flexWrap: "wrap" }}>
          <span style={{ fontSize: 11, color: "var(--text-ghost)", fontWeight: 600 }}>AIR-GAP TEST FIXTURES:</span>
          {[
            { id: "day_crossing.mp4", label: "☀️ Day Crossing" },
            { id: "night_crossing.mp4", label: "🌙 Night Crossing" },
            { id: "vehicle_plate.mp4", label: "🚗 Vehicle Plate" },
            { id: "loitering.mp4", label: "⏱️ Loitering" },
          ].map((fx) => (
            <button
              key={fx.id}
              type="button"
              className="btn btn-secondary btn-sm"
              style={{ fontSize: 11, padding: "4px 10px", borderColor: "rgba(0, 240, 255, 0.4)", color: "#00f0ff" }}
              onClick={async (e) => {
                e.stopPropagation();
                try {
                  setUploadSuccess(`Importing sample fixture ${fx.id}...`);
                  const asset = await api.importSample(fx.id);
                  setUploadSuccess(`✓ Loaded sample ${fx.id} (Asset #${asset.id}). Ready for analysis!`);
                  refreshData();
                } catch (err: any) {
                  setUploadError(`Failed to import sample: ${err.message}`);
                }
              }}
            >
              {fx.label}
            </button>
          ))}
        </div>

        {uploadProgress !== null && (
          <div style={{ marginTop: 10, maxWidth: 300, margin: "10px auto 0" }}>
            <span style={{ color: "#00f0ff", fontSize: 11 }}>Ingesting video file...</span>
          </div>
        )}
      </div>

      {uploadError && <div className="test-feedback fail" style={{ marginBottom: 16 }}>{uploadError}</div>}
      {uploadSuccess && <div className="test-feedback success" style={{ marginBottom: 16 }}>{uploadSuccess}</div>}

      {/* Main Studio 2-Column Grid */}
      <div style={{ display: "grid", gridTemplateColumns: "360px 1fr", gap: 16 }}>
        {/* Left Column: Media Library & Jobs */}
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {/* Media Assets Library */}
          <div className="panel" style={{ padding: 14 }}>
            <div className="panel-header" style={{ marginBottom: 10 }}>
              <span className="panel-title" style={{ fontSize: 12 }}>SURVEILLANCE MEDIA ASSETS</span>
              <span className="panel-tag">{assets.length} UPLOADED</span>
            </div>

            {assets.length === 0 ? (
              <div style={{ textAlign: "center", padding: "30px 10px", color: "var(--text-ghost)", fontSize: 12 }}>
                No data yet. Upload surveillance footage to begin edge analysis.
              </div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 8, maxHeight: 280, overflowY: "auto" }}>
                {assets.map((asset) => (
                  <div
                    key={asset.id}
                    style={{
                      background: "rgba(0,0,0,0.3)",
                      border: "1px solid rgba(255,255,255,0.08)",
                      borderRadius: 6,
                      padding: 10,
                    }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
                      <b style={{ color: "#fff", fontSize: 12, wordBreak: "break-all" }}>
                        {asset.original_filename}
                      </b>
                      <span style={{ fontSize: 9, color: "var(--text-ghost)", fontFamily: "var(--font-mono)" }}>
                        {asset.duration_seconds?.toFixed(1) || "?"}s
                      </span>
                    </div>
                    <div style={{ fontSize: 10, color: "var(--text-secondary)", marginTop: 4 }}>
                      {asset.width}x{asset.height} @ {asset.fps?.toFixed(1) || 25} FPS • {(asset.file_size_bytes / 1024 / 1024).toFixed(1)} MB
                    </div>
                    <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
                      <button
                        className="btn btn-sm btn-primary"
                        style={{ flex: 1, padding: "3px 8px", fontSize: 10 }}
                        onClick={() => setSelectedAssetForJob(asset)}
                      >
                        🚀 Run CV Analysis
                      </button>
                      <button
                        className="btn btn-sm btn-secondary"
                        style={{ padding: "3px 8px", fontSize: 10 }}
                        onClick={() => setPreviewMedia(asset)}
                      >
                        ▶ Stream
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Analysis Jobs */}
          <div className="panel" style={{ padding: 14 }}>
            <div className="panel-header" style={{ marginBottom: 10 }}>
              <span className="panel-title" style={{ fontSize: 12 }}>ANALYSIS RUNS</span>
              <span className="panel-tag">{jobs.length} JOBS</span>
            </div>

            {jobs.length === 0 ? (
              <div style={{ textAlign: "center", padding: "30px 10px", color: "var(--text-ghost)", fontSize: 12 }}>
                No data yet. Launch a pipeline job from an uploaded video above.
              </div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 6, maxHeight: 300, overflowY: "auto" }}>
                {jobs.map((job: any) => {
                  const jid = String(job.id || job.job_id);
                  const isSelected = jid === String(activeJobId);
                  const isDone = job.status === "completed" || job.status === "COMPLETED";
                  return (
                    <div
                      key={jid}
                      onClick={() => setActiveJobId(jid)}
                      style={{
                        background: isSelected ? "rgba(0, 240, 255, 0.12)" : "rgba(0,0,0,0.3)",
                        border: isSelected ? "1px solid #00f0ff" : "1px solid rgba(255,255,255,0.06)",
                        borderRadius: 6,
                        padding: 10,
                        cursor: "pointer",
                      }}
                    >
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                        <span style={{ fontWeight: 600, color: isSelected ? "#00f0ff" : "#fff", fontSize: 11 }}>
                          Job #{jid} ({job.detector_model})
                        </span>
                        <span
                          style={{
                            fontSize: 9,
                            padding: "1px 5px",
                            borderRadius: 3,
                            color: isDone ? "#00ff9d" : "#ffaa00",
                            border: `1px solid ${isDone ? "#00ff9d" : "#ffaa00"}`,
                          }}
                        >
                          {job.status?.toUpperCase()}
                        </span>
                      </div>
                      <div style={{ fontSize: 10, color: "var(--text-secondary)", marginTop: 4 }}>
                        Detections: {job.detections_count} • Incidents: {job.incidents_count} • Progress: {job.progress_percent?.toFixed(0) || 0}%
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>

        {/* Right Column: Active Job Telemetry, Reports, Detections */}
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {/* Active Job Telemetry Panel & Report Buttons */}
          <div className="panel" style={{ padding: 16 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 12 }}>
              <div>
                <span className="panel-title" style={{ fontSize: 13 }}>
                  EDGE COMPUTER VISION TELEMETRY
                </span>
                {activeJob && (
                  <div style={{ fontSize: 11, color: "var(--text-secondary)", marginTop: 2 }}>
                    JOB #{activeJob.id || activeJob.job_id} • MODEL: {activeJob.detector_model?.toUpperCase()} • WS: {wsConnected ? "🟢 LIVE" : "⚪ OFFLINE"}
                  </div>
                )}
              </div>

              {/* Task 4.3 Report Export Buttons */}
              {activeJob && (
                <div style={{ display: "flex", gap: 6 }}>
                  <a
                    href={api.analysisJobReportUrl(activeJob.id || activeJob.job_id, "json")}
                    download={`ibvap_report_job_${activeJob.id || activeJob.job_id}.json`}
                    className="btn btn-sm btn-secondary"
                    target="_blank"
                    rel="noreferrer"
                    data-testid="evidence-export-json"
                  >
                    📄 Export JSON
                  </a>
                  <a
                    href={api.analysisJobReportUrl(activeJob.id || activeJob.job_id, "pdf")}
                    download={`ibvap_report_job_${activeJob.id || activeJob.job_id}.pdf`}
                    className="btn btn-sm btn-primary"
                    target="_blank"
                    rel="noreferrer"
                    data-testid="evidence-export-pdf"
                  >
                    📑 Export PDF (BSA §63)
                  </a>
                </div>
              )}
            </div>

            {!activeJob ? (
              <div style={{ textAlign: "center", padding: "50px 10px", color: "var(--text-ghost)", fontSize: 12 }}>
                No analysis job selected. Select a job from the left or upload footage to run edge analysis.
              </div>
            ) : (
              <div>
                {/* Progress Bar */}
                <div style={{ marginBottom: 14 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, marginBottom: 4 }}>
                    <span style={{ color: "var(--text-secondary)" }}>
                      Processed: <b>{activeJob.processed_frames}</b> / {activeJob.total_frames || activeJob.processed_frames} Frames
                    </span>
                    <span style={{ color: "#00f0ff", fontFamily: "var(--font-mono)", fontWeight: 700 }}>
                      {activeJob.progress_percent?.toFixed(1) || 0}%
                    </span>
                  </div>
                  <div style={{ height: 8, background: "rgba(255,255,255,0.08)", borderRadius: 4, overflow: "hidden" }}>
                    <div
                      data-testid="progress-bar"
                      style={{
                        height: "100%",
                        width: `${Math.min(100, Math.max(0, activeJob.progress_percent || 0))}%`,
                        background: activeJob.status === "completed" ? "#00ff9d" : "linear-gradient(90deg, #00f0ff, #00ff9d)",
                        transition: "width 0.2s ease-out",
                      }}
                    />
                  </div>
                </div>

                {/* HUD Cards */}
                <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 10 }}>
                  <div style={{ background: "rgba(0,0,0,0.3)", padding: "8px", borderRadius: 4, textAlign: "center" }}>
                    <div style={{ fontSize: 9, color: "var(--text-ghost)" }}>CV SPEED</div>
                    <div style={{ fontSize: 16, fontWeight: 700, color: "#00f0ff", fontFamily: "var(--font-mono)" }}>
                      {activeJob.fps?.toFixed(1) || "0.0"} <span style={{ fontSize: 9 }}>FPS</span>
                    </div>
                  </div>
                  <div style={{ background: "rgba(0,0,0,0.3)", padding: "8px", borderRadius: 4, textAlign: "center" }}>
                    <div style={{ fontSize: 9, color: "var(--text-ghost)" }}>DETECTIONS</div>
                    <div style={{ fontSize: 16, fontWeight: 700, color: "#00ff9d", fontFamily: "var(--font-mono)" }}>
                      {activeJob.detections_count}
                    </div>
                  </div>
                  <div style={{ background: "rgba(0,0,0,0.3)", padding: "8px", borderRadius: 4, textAlign: "center" }}>
                    <div style={{ fontSize: 9, color: "var(--text-ghost)" }}>INCIDENTS</div>
                    <div style={{ fontSize: 16, fontWeight: 700, color: activeJob.incidents_count > 0 ? "#ff2a55" : "#94a3b8", fontFamily: "var(--font-mono)" }}>
                      {activeJob.incidents_count}
                    </div>
                  </div>
                  <div style={{ background: "rgba(0,0,0,0.3)", padding: "8px", borderRadius: 4, textAlign: "center" }}>
                    <div style={{ fontSize: 9, color: "var(--text-ghost)" }}>NIGHT MODE</div>
                    <div style={{ fontSize: 14, fontWeight: 700, color: activeJob.summary?.is_night ? "#00f0ff" : "#94a3b8" }}>
                      {activeJob.summary?.is_night ? "🌙 NIGHT" : "☀ DAY"}
                    </div>
                  </div>
                </div>

                {/* Task 1 & Task 2.2: Tactical HUD Video Player with Track Trails Canvas */}
                <div style={{ marginTop: 14 }}>
                  <TacticalHUD
                    cameraId={activeJob.source_id}
                    cameraName={`SOURCE: ${(activeJob.source_type || 'MEDIA').toUpperCase()} #${activeJob.source_id || activeJob.id}`}
                    fps={activeJob.fps}
                    resolution={`${activeJob.total_frames} Frames`}
                    status={activeJob.status}
                    isRecording={activeJob.status === "processing"}
                    recTimestamp={`F#${activeJob.processed_frames}`}
                    defconLevel={activeJob.incidents_count > 0 ? 2 : 4}
                    variant="card"
                  >
                    <div style={{ position: "relative", background: "#000", minHeight: 260, display: "flex", justifyContent: "center", alignItems: "center", overflow: "hidden" }}>
                      {(activeJob.source_type === "upload" || activeJob.source_type === "media") && (activeJob.source_id || activeJob.id) ? (
                        <div style={{ position: "relative", display: "inline-block", maxWidth: "100%", lineHeight: 0 }}>
                          <video
                            ref={videoPlayerRef}
                            controls
                            src={`/api/v1/media/${activeJob.source_id || activeJob.id}/stream`}
                            className={sensorSkin === "nvg" ? "tactical-sensor-skin-nvg" : sensorSkin === "flir" ? "tactical-sensor-skin-flir" : ""}
                            onTimeUpdate={(e) => drawTrailsOnCanvas(e.currentTarget.currentTime)}
                            onLoadedMetadata={() => drawTrailsOnCanvas(videoPlayerRef.current?.currentTime || 0)}
                            onPlay={startPlaybackSync}
                            onPause={stopPlaybackSync}
                            onEnded={stopPlaybackSync}
                            onSeeked={() => drawTrailsOnCanvas(videoPlayerRef.current?.currentTime || 0)}
                            style={{ width: "100%", maxHeight: 360, display: "block" }}
                          />
                          {/* Canvas Overlay for Track Trails & Bounding Boxes (60fps animation sync) */}
                          <canvas
                            ref={trailCanvasRef}
                            style={{
                              position: "absolute",
                              top: 0,
                              left: 0,
                              width: "100%",
                              height: "100%",
                              pointerEvents: "none",
                              zIndex: 4,
                            }}
                          />
                        </div>
                      ) : (
                        <div style={{ color: "var(--text-ghost)", padding: 30, fontSize: 12 }}>
                          Footage stream ready. Analysis evaluated {activeJob.processed_frames} frames.
                        </div>
                      )}

                      {/* Sensor Skin Disclaimer Banner (Task 7.1) */}
                      {ENABLE_SENSOR_SKINS && sensorSkin !== "raw" && (
                        <div className="tactical-skin-disclaimer">
                          ⚠️ VISUALIZATION ONLY — DETECTION RUNS ON RAW FRAMES
                        </div>
                      )}
                    </div>

                    {/* Sensor Skin Controls (Task 7) */}
                    {ENABLE_SENSOR_SKINS && (
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "6px 12px", background: "rgba(0,0,0,0.4)", borderTop: "1px solid rgba(255,255,255,0.08)" }}>
                        <span style={{ fontSize: 10, color: "var(--text-secondary)" }}>SENSOR SKIN PREVIEW:</span>
                        <div style={{ display: "flex", gap: 6 }}>
                          <button
                            className={`btn btn-sm ${sensorSkin === "raw" ? "btn-primary" : "btn-secondary"}`}
                            style={{ fontSize: 9, padding: "2px 8px" }}
                            onClick={() => setSensorSkin("raw")}
                          >
                            Normal Raw
                          </button>
                          <button
                            className={`btn btn-sm ${sensorSkin === "nvg" ? "btn-primary" : "btn-secondary"}`}
                            style={{ fontSize: 9, padding: "2px 8px" }}
                            onClick={() => setSensorSkin("nvg")}
                          >
                            🌙 NVG Green
                          </button>
                          <button
                            className={`btn btn-sm ${sensorSkin === "flir" ? "btn-primary" : "btn-secondary"}`}
                            style={{ fontSize: 9, padding: "2px 8px" }}
                            onClick={() => setSensorSkin("flir")}
                          >
                            🔥 FLIR Thermal
                          </button>
                        </div>
                      </div>
                    )}
                  </TacticalHUD>
                </div>

                {/* Task 3: Active Tracks Roster Panel */}
                <div style={{ marginTop: 14 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <span style={{ fontSize: 11, fontWeight: 700, color: "#00f0ff" }}>
                        🎯 ACTIVE TRACKS ROSTER ({jobTracks.length})
                      </span>
                      {selectedTrackId && (
                        <button
                          className="btn btn-sm btn-secondary"
                          style={{ fontSize: 9, padding: "1px 6px", color: "#ffaa00", borderColor: "#ffaa00" }}
                          onClick={() => handleSelectTrack(selectedTrackId)}
                        >
                          ✕ Clear Filter ({selectedTrackId})
                        </button>
                      )}
                    </div>
                    <span style={{ fontSize: 9, color: "var(--text-secondary)" }}>
                      Click track chip to seek video & draw polyline
                    </span>
                  </div>

                  {jobTracks.length === 0 ? (
                    <div style={{ textAlign: "center", padding: "12px", background: "rgba(0,0,0,0.25)", borderRadius: 4, color: "var(--text-ghost)", fontSize: 11 }}>
                      No active tracks
                    </div>
                  ) : (
                    <div className="tactical-tracks-roster">
                      {jobTracks.map((tr) => {
                        const isSelected = selectedTrackId === tr.track_id;
                        const fps = activeJob?.fps || 15.0;
                        const dwellSec = ((tr.last_frame - tr.first_frame) / fps).toFixed(1);
                        const icon = tr.class === "person" ? "👤" : tr.class === "car" || tr.class === "vehicle" ? "🚗" : "⚡";

                        return (
                          <div
                            key={tr.track_id}
                            className={`tactical-track-card ${isSelected ? "active" : ""}`}
                            onClick={() => handleSelectTrack(tr.track_id)}
                          >
                            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                              <span style={{ fontSize: 14 }}>{icon}</span>
                              <span style={{ fontWeight: 700, color: isSelected ? "#00ff9d" : "#00f0ff", fontSize: 11 }}>
                                {tr.track_id}
                              </span>
                              <span style={{ fontSize: 10, color: "var(--text-secondary)", textTransform: "uppercase" }}>
                                {tr.class}
                              </span>
                              {tr.zones_touched && tr.zones_touched.length > 0 && (
                                <span style={{ fontSize: 9, background: "rgba(0, 240, 255, 0.15)", color: "#00f0ff", padding: "1px 5px", borderRadius: 3 }}>
                                  {tr.zones_touched[0]}
                                </span>
                              )}
                              {tr.night_frame_pct > 0 && (
                                <span style={{ fontSize: 9, background: "rgba(56, 189, 248, 0.2)", color: "#38bdf8", padding: "1px 5px", borderRadius: 3 }}>
                                  🌙 NIGHT
                                </span>
                              )}
                            </div>

                            <div style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 10 }}>
                              <span style={{ color: "var(--text-ghost)" }}>Dwell: <b style={{ color: "#fff" }}>{dwellSec}s</b></span>
                              <span style={{ color: "var(--text-ghost)" }}>Speed: <b style={{ color: "#00ff9d" }}>{tr.max_speed} px/s</b></span>
                              <span style={{ color: "var(--text-ghost)" }}>Pts: <b style={{ color: "#38bdf8" }}>{tr.points_count}</b></span>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>

          {/* Incidents & Detections Feed */}
          <div className="panel" style={{ padding: 16, flex: 1 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
              <span className="panel-title" style={{ fontSize: 13 }}>
                INCIDENTS & DETECTION FEED
              </span>

              {/* Task 5.2 Filters */}
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <select
                  value={filterClass}
                  onChange={(e) => setFilterClass(e.target.value)}
                  style={{ background: "#080c14", border: "1px solid rgba(255,255,255,0.2)", color: "#fff", padding: "4px 8px", borderRadius: 4, fontSize: 11 }}
                >
                  <option value="">All Classes</option>
                  <option value="person">Persons Only</option>
                  <option value="vehicle">Vehicles Only</option>
                  <option value="motion">Motion Only</option>
                </select>

                <label style={{ fontSize: 11, color: "var(--text-secondary)", display: "flex", alignItems: "center", gap: 4 }}>
                  <input
                    type="checkbox"
                    checked={filterNightOnly}
                    onChange={(e) => setFilterNightOnly(e.target.checked)}
                  />
                  🌙 Night Only
                </label>
              </div>
            </div>

            {/* Incidents List */}
            {activeResults?.incidents && activeResults.incidents.length > 0 && (
              <div style={{ marginBottom: 16 }}>
                <div style={{ fontSize: 11, fontWeight: 700, color: "#ff2a55", marginBottom: 6 }}>
                  🚨 FLAGGED INCIDENTS ({activeResults.incidents.length})
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  {activeResults.incidents.map((inc: any) => (
                    <div
                      key={inc.id}
                      style={{
                        background: "rgba(255, 42, 85, 0.08)",
                        border: "1px solid rgba(255, 42, 85, 0.3)",
                        borderRadius: 6,
                        padding: "8px 12px",
                      }}
                    >
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                        <span style={{ fontWeight: 700, color: "#ff2a55", fontSize: 12 }}>
                          {inc.title}
                        </span>
                        <div style={{ display: "flex", gap: 6 }}>
                          {inc.zone_name && (
                            <span style={{ fontSize: 9, background: "rgba(0, 240, 255, 0.2)", color: "#00f0ff", padding: "1px 6px", borderRadius: 3 }}>
                              ZONE: {inc.zone_name}
                            </span>
                          )}
                          <span style={{ fontSize: 9, background: "#ff2a55", color: "#000", fontWeight: "bold", padding: "1px 6px", borderRadius: 3 }}>
                            SEV: {inc.severity}
                          </span>
                        </div>
                      </div>
                      <div style={{ fontSize: 11, color: "var(--text-secondary)", marginTop: 4 }}>
                        {inc.description}
                      </div>
                      {inc.track_ids && inc.track_ids.length > 0 && (
                        <div style={{ fontSize: 10, color: "#00f0ff", marginTop: 4 }}>
                          Tracks: {inc.track_ids.join(", ")}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Task 4.1 Evidence Gallery with Inline Playback & SHA-256 Verification */}
            {activeResults?.evidence && activeResults.evidence.length > 0 && (
              <div style={{ marginBottom: 16 }}>
                <div style={{ fontSize: 11, fontWeight: 700, color: "#00ff9d", marginBottom: 6 }}>
                  🛡 BSA 2023 §63 FORENSIC EVIDENCE ({activeResults.evidence.length})
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: 10 }}>
                  {activeResults.evidence.map((ev: any) => {
                    const fname = ev.file_path ? ev.file_path.split("/").pop() : "";
                    const isVideo = fname.endsWith(".mp4");
                    const vRes = evidenceVerifyStatus[ev.id];

                    return (
                      <div
                        key={ev.id}
                        style={{
                          background: "rgba(0,0,0,0.4)",
                          border: "1px solid rgba(0, 255, 157, 0.2)",
                          borderRadius: 6,
                          padding: 10,
                          fontSize: 10,
                        }}
                      >
                        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                          <b style={{ color: "#fff" }}>Evidence #{ev.id} ({ev.evidence_type?.toUpperCase()})</b>
                          <span style={{ color: "var(--text-ghost)" }}>{(ev.file_size_bytes / 1024).toFixed(0)} KB</span>
                        </div>

                        {/* Inline video or image playback */}
                        {isVideo ? (
                          <div style={{ borderRadius: 4, overflow: "hidden", marginBottom: 6, background: "#000" }}>
                            <video
                              controls
                              src={`/api/v1/evidence/vault/clips/${fname}?token=${encodeURIComponent((typeof localStorage !== "undefined" ? (localStorage.getItem("ibvap_token") || localStorage.getItem("token")) : "") || "")}`}
                              style={{ width: "100%", maxHeight: 140, display: "block" }}
                            />
                          </div>
                        ) : (
                          <div style={{ borderRadius: 4, overflow: "hidden", marginBottom: 6, background: "#000" }}>
                            <img
                              src={`/api/v1/evidence/vault/clips/${fname}?token=${encodeURIComponent((typeof localStorage !== "undefined" ? (localStorage.getItem("ibvap_token") || localStorage.getItem("token")) : "") || "")}`}
                              alt="Evidence snapshot"
                              style={{ width: "100%", height: 100, objectFit: "cover" }}
                              onError={(e) => {
                                (e.target as HTMLElement).style.display = "none";
                              }}
                            />
                          </div>
                        )}

                        <div style={{ fontFamily: "var(--font-mono)", fontSize: 8, color: "#00ff9d", wordBreak: "break-all", background: "rgba(0,0,0,0.3)", padding: 4, borderRadius: 3 }}>
                          SHA: {ev.sha256}
                        </div>

                        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 6 }}>
                          <button
                            className="btn btn-sm btn-secondary"
                            style={{ fontSize: 9, padding: "2px 6px" }}
                            onClick={() => handleVerifyEvidence(ev.id)}
                          >
                            🔍 Verify Hash
                          </button>

                          {vRes && (
                            <span
                              style={{
                                fontSize: 9,
                                fontWeight: "bold",
                                color: vRes.match ? "#00ff9d" : "#ff2a55",
                              }}
                            >
                              {vRes.match ? "✓ MATCH (BSA §63)" : "✗ MISMATCH"}
                            </span>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {/* Task 5.2 Real Object Detections Feed */}
            <div>
              <div style={{ fontSize: 11, fontWeight: 700, color: "#00f0ff", marginBottom: 6 }}>
                🎯 REAL FRAME DETECTIONS FEED ({filteredDetections.length})
              </div>

              {filteredDetections.length === 0 ? (
                <div style={{ textAlign: "center", padding: "30px 10px", color: "var(--text-ghost)", fontSize: 12 }}>
                  {activeJob?.status === "processing" ? "Analyzing frames..." : "No data yet."}
                </div>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: 6, maxHeight: 250, overflowY: "auto" }}>
                  {filteredDetections.slice(0, 30).map((det: any, idx: number) => {
                    const payload = det.payload || det.metadata || {};
                    const isNight = payload.night;
                    const plateText = payload.plate_text;

                    return (
                      <div
                        key={idx}
                        className="tactical-track-card"
                        data-testid="detection-card"
                        style={{
                          background: "rgba(0,0,0,0.25)",
                          border: "1px solid rgba(255, 255, 255, 0.06)",
                          borderRadius: 4,
                          padding: "6px 10px",
                          display: "flex",
                          justifyContent: "space-between",
                          fontSize: 11,
                          alignItems: "center",
                        }}
                      >
                        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                          {/* Class tag */}
                          <span style={{ color: "#00f0ff", fontWeight: "bold", textTransform: "uppercase" }}>
                            {det.label}
                          </span>

                          {/* Task 5.2 & Task 2.2 track_id chip with click-to-track */}
                          {det.track_id && (
                            <span
                              onClick={(e) => {
                                e.stopPropagation();
                                handleSelectTrack(String(det.track_id));
                              }}
                              style={{
                                background: selectedTrackId === String(det.track_id) ? "#00f0ff" : "rgba(0, 240, 255, 0.15)",
                                color: selectedTrackId === String(det.track_id) ? "#000" : "#00f0ff",
                                padding: "1px 5px",
                                borderRadius: 3,
                                fontSize: 9,
                                fontFamily: "var(--font-mono)",
                                cursor: "pointer",
                                fontWeight: "bold",
                              }}
                              title="Click to seek video and highlight track path"
                            >
                              TRK-{det.track_id}
                            </span>
                          )}

                          {/* Task 5.2 plate text column/chip */}
                          {plateText && (
                            <span style={{ background: "rgba(255, 255, 255, 0.15)", color: "#fff", padding: "1px 6px", borderRadius: 3, fontSize: 9, fontWeight: 700, border: "1px solid #fff" }}>
                              🚗 {plateText}
                            </span>
                          )}

                          {/* Task 5.2 NIGHT badge */}
                          {isNight && (
                            <span style={{ background: "rgba(56, 189, 248, 0.2)", color: "#38bdf8", padding: "1px 5px", borderRadius: 3, fontSize: 9, fontWeight: 700 }}>
                              🌙 NIGHT
                            </span>
                          )}

                          <span style={{ color: "var(--text-ghost)", fontSize: 10 }}>
                            F#{det.frame || det.frame_index || idx}
                          </span>
                        </div>

                        <div style={{ color: "#00ff9d", fontWeight: "bold", fontFamily: "var(--font-mono)" }}>
                          {((det.confidence || 0) * 100).toFixed(1)}%
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Task 5.1 Virtual Fence & Zone Studio Modal */}
      {showZoneEditor && (
        <div className="section-65b-modal-backdrop" onClick={() => setShowZoneEditor(false)}>
          <div
            className="panel"
            style={{ maxWidth: 880, width: "95%", margin: 0, padding: 20 }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
              <div>
                <h3 style={{ margin: 0, color: "#00f0ff" }}>Virtual Fence & Tactical Zone Studio</h3>
                <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>
                  Click canvas to define directional tripwire lines or intrusion polygons (coordinates normalized 0..1)
                </div>
              </div>
              <button className="btn btn-sm btn-secondary" onClick={() => setShowZoneEditor(false)}>
                ✕ Close
              </button>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 280px", gap: 16 }}>
              {/* Drawing Canvas */}
              <div>
                <canvas
                  ref={canvasRef}
                  width={560}
                  height={320}
                  onClick={handleCanvasClick}
                  data-testid="zone-canvas"
                  style={{
                    width: "100%",
                    height: 320,
                    borderRadius: 6,
                    border: "1px solid #00f0ff",
                    cursor: "crosshair",
                    display: "block",
                  }}
                />
                <div style={{ display: "flex", justifyContent: "space-between", marginTop: 8, fontSize: 10, color: "var(--text-ghost)" }}>
                  <span>Points Recorded: {zonePoints.length}</span>
                  <button
                    className="btn btn-sm btn-secondary"
                    style={{ fontSize: 10, padding: "2px 8px" }}
                    onClick={() => setZonePoints([])}
                  >
                    Clear Points
                  </button>
                </div>
              </div>

              {/* Controls */}
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                <div>
                  <label style={{ fontSize: 11, color: "var(--text-secondary)" }}>Zone Name:</label>
                  <input
                    type="text"
                    value={zoneName}
                    onChange={(e) => setZoneName(e.target.value)}
                    placeholder="e.g. Sector 4 Line Tripwire"
                    data-testid="zone-name-input"
                    style={{ width: "100%", background: "#040b14", border: "1px solid #333", color: "#fff", padding: 6, borderRadius: 4, marginTop: 2, fontSize: 11 }}
                  />
                </div>

                <div>
                  <label style={{ fontSize: 11, color: "var(--text-secondary)" }}>Geometry Type:</label>
                  <div style={{ display: "flex", gap: 6, marginTop: 2 }}>
                    <button
                      className={`btn btn-sm ${zoneDrawMode === "line" ? "btn-primary" : "btn-secondary"}`}
                      style={{ flex: 1, fontSize: 10 }}
                      data-testid="zone-type-line"
                      onClick={() => {
                        setZoneDrawMode("line");
                        setZonePoints([]);
                      }}
                    >
                      Tripwire Line (2 pts)
                    </button>
                    <button
                      className={`btn btn-sm ${zoneDrawMode === "polygon" ? "btn-primary" : "btn-secondary"}`}
                      style={{ flex: 1, fontSize: 10 }}
                      data-testid="zone-type-polygon"
                      onClick={() => {
                        setZoneDrawMode("polygon");
                        setZonePoints([]);
                      }}
                    >
                      Perimeter Polygon (3+ pts)
                    </button>
                  </div>
                </div>

                {zoneDrawMode === "line" && (
                  <div>
                    <label style={{ fontSize: 11, color: "var(--text-secondary)" }}>Crossing Direction:</label>
                    <select
                      value={zoneDirection}
                      onChange={(e) => setZoneDirection(e.target.value)}
                      data-testid="zone-direction"
                      style={{ width: "100%", background: "#040b14", border: "1px solid #333", color: "#fff", padding: 6, borderRadius: 4, marginTop: 2, fontSize: 11 }}
                    >
                      <option value="either">Bidirectional (Either Direction)</option>
                      <option value="a_to_b">Directional A → B Only</option>
                      <option value="b_to_a">Directional B → A Only</option>
                    </select>
                  </div>
                )}

                <div>
                  <label style={{ fontSize: 11, color: "var(--text-secondary)", display: "flex", alignItems: "center", gap: 6 }}>
                    <input
                      type="checkbox"
                      checked={zoneNightOnly}
                      onChange={(e) => setZoneNightOnly(e.target.checked)}
                    />
                    Night-Only Gating (Fire only when luma &lt; 60)
                  </label>
                </div>

                <div>
                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10 }}>
                    <span style={{ color: "var(--text-secondary)" }}>Min Confidence</span>
                    <span style={{ color: "#00f0ff" }}>{(zoneMinConfidence * 100).toFixed(0)}%</span>
                  </div>
                  <input
                    type="range"
                    min="0.10"
                    max="0.90"
                    step="0.05"
                    value={zoneMinConfidence}
                    onChange={(e) => setZoneMinConfidence(parseFloat(e.target.value))}
                    style={{ width: "100%" }}
                  />
                </div>

                <button className="btn btn-primary" onClick={handleSaveZone} style={{ marginTop: 6 }} data-testid="zone-save">
                  💾 Save Virtual Fence Zone
                </button>
              </div>
            </div>

            {/* List of existing zones */}
            <div style={{ marginTop: 16, borderTop: "1px solid rgba(255,255,255,0.08)", paddingTop: 12 }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: "#00f0ff", marginBottom: 6 }}>
                ACTIVE VIRTUAL FENCES ({zones.length})
              </div>
              {zones.length === 0 ? (
                <div style={{ fontSize: 10, color: "var(--text-ghost)" }}>No zones defined yet.</div>
              ) : (
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 8 }}>
                  {zones.map((z: any) => (
                    <div
                      key={z.id}
                      style={{
                        background: "rgba(0,0,0,0.3)",
                        border: "1px solid rgba(255,255,255,0.06)",
                        borderRadius: 4,
                        padding: 8,
                        fontSize: 10,
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                      }}
                    >
                      <div>
                        <b style={{ color: "#fff" }}>{z.name}</b>
                        <div style={{ color: "var(--text-ghost)", marginTop: 2 }}>
                          {z.geometry?.type?.toUpperCase()} • {z.direction} {z.night_only ? "• 🌙 Night" : ""}
                        </div>
                      </div>
                      <button
                        className="btn btn-sm btn-danger"
                        style={{ padding: "2px 6px", fontSize: 9 }}
                        onClick={() => handleDeleteZone(z.id)}
                      >
                        ✕
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Launch Analysis Modal with Zone Selection & Feature Toggles */}
      {selectedAssetForJob && (
        <div className="section-65b-modal-backdrop" onClick={() => setSelectedAssetForJob(null)}>
          <div
            className="panel"
            style={{ maxWidth: 540, width: "95%", margin: 0, padding: 20 }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
              <h3 style={{ margin: 0, color: "#fff" }}>Configure Edge CV Pipeline</h3>
              <button className="btn btn-sm btn-secondary" onClick={() => setSelectedAssetForJob(null)}>
                ✕
              </button>
            </div>

            <div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 12 }}>
              Footage: <b style={{ color: "#00f0ff" }}>{selectedAssetForJob.original_filename}</b> • {selectedAssetForJob.width}x{selectedAssetForJob.height} @ {selectedAssetForJob.fps?.toFixed(1) || 25} FPS
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <div>
                <label style={{ fontSize: 10, color: "var(--text-ghost)", display: "block", marginBottom: 4 }}>
                  DETECTOR MODEL
                </label>
                <select
                  value={detectorModel}
                  onChange={(e) => setDetectorModel(e.target.value)}
                  style={{ width: "100%", background: "#080c14", border: "1px solid #333", color: "#fff", padding: 6, borderRadius: 4, fontSize: 11 }}
                >
                  <option value="yolo11n">Ultralytics YOLO26/11 Neural Network</option>
                  <option value="motion_mog2">OpenCV MOG2 Motion Subtractor</option>
                </select>
              </div>

              {/* Arm Virtual Fences */}
              <div>
                <label style={{ fontSize: 10, color: "var(--text-ghost)", display: "block", marginBottom: 4 }}>
                  ARM VIRTUAL FENCES ({zones.length} Available)
                </label>
                {zones.length === 0 ? (
                  <div style={{ fontSize: 10, color: "var(--text-ghost)" }}>
                    No zones defined. You can create zones via the "Virtual Fence Studio" button.
                  </div>
                ) : (
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                    {zones.map((z: any) => {
                      const isArmed = Array.isArray(selectedZoneIds) && selectedZoneIds.includes(z.id);
                      return (
                        <button
                          key={z.id}
                          type="button"
                          className={`btn btn-sm ${isArmed ? "btn-primary" : "btn-secondary"}`}
                          style={{ fontSize: 10 }}
                          onClick={() => {
                            if (isArmed) {
                              setSelectedZoneIds(selectedZoneIds.filter((id) => id !== z.id));
                            } else {
                              setSelectedZoneIds([...selectedZoneIds, z.id]);
                            }
                          }}
                        >
                          {isArmed ? "✓" : "+"} {z.name}
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>

              {/* Intelligence Modules Toggles */}
              <div>
                <label style={{ fontSize: 10, color: "var(--text-ghost)", display: "block", marginBottom: 6 }}>
                  CV INTELLIGENCE MODULES
                </label>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, fontSize: 11, color: "var(--text-secondary)" }}>
                  <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
                    <input type="checkbox" checked={enableTracking} onChange={(e) => setEnableTracking(e.target.checked)} />
                    Centroid Tracker (IDs)
                  </label>
                  <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
                    <input type="checkbox" checked={enableAnpr} onChange={(e) => setEnableAnpr(e.target.checked)} />
                    ANPR Plate Localization
                  </label>
                  <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
                    <input type="checkbox" checked={enableBehavior} onChange={(e) => setEnableBehavior(e.target.checked)} />
                    Behavior Rules Engine
                  </label>
                  <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
                    <input type="checkbox" checked={enableNightMode} onChange={(e) => setEnableNightMode(e.target.checked)} />
                    Night Luma & CLAHE
                  </label>
                  <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
                    <input type="checkbox" checked={enableFace} onChange={(e) => setEnableFace(e.target.checked)} />
                    Face Watchlist Match
                  </label>
                </div>
              </div>

              <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 8 }}>
                <button className="btn btn-secondary" onClick={() => setSelectedAssetForJob(null)}>
                  Cancel
                </button>
                <button className="btn btn-primary" onClick={handleLaunchAnalysis} data-testid="analyze-button">
                  🚀 Launch Pipeline Job
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Stream Video Modal */}
      {previewMedia && (
        <div className="section-65b-modal-backdrop" onClick={() => setPreviewMedia(null)}>
          <div
            className="panel"
            style={{ maxWidth: 720, width: "95%", margin: 0, padding: 16 }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
              <h3 style={{ margin: 0, color: "#fff", fontSize: 13 }}>Stream: {previewMedia.original_filename}</h3>
              <button className="btn btn-sm btn-secondary" onClick={() => setPreviewMedia(null)}>
                ✕ Close
              </button>
            </div>
            <video
              controls
              autoPlay
              muted
              playsInline
              preload="auto"
              src={`/api/v1/media/${previewMedia.id}/stream`}
              style={{ width: "100%", maxHeight: 400, borderRadius: 4, background: "#000" }}
            />
          </div>
        </div>
      )}
    </div>
  );
}
