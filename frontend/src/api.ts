/* IBVAP API client */

function getApiBase(): string {
  if (import.meta.env.VITE_API_URL) {
    return `${import.meta.env.VITE_API_URL.replace(/\/$/, '')}/api/v1`;
  }
  // Relative /api/v1 routes seamlessly through Vite proxy on port 5173 and FastAPI on port 8001
  return '/api/v1';
}

import { telemetry } from './utils/telemetry';

const BASE = getApiBase();

export function getAuthToken(): string | null {
  if (typeof localStorage === 'undefined') return null;
  return localStorage.getItem('ibvap_token') || localStorage.getItem('token');
}

async function request<T>(path: string, opts?: RequestInit): Promise<T> {
  const token = getAuthToken();
  const traceId = telemetry.generateTraceId();
  const headers: Record<string, string> = { ...(opts?.headers as Record<string, string> || {}) };
  if (token) headers['Authorization'] = `Bearer ${token}`;
  headers['X-Trace-ID'] = traceId;

  const start = performance.now();
  try {
    const res = await fetch(BASE + path, { ...opts, headers });
    const durationMs = Math.round(performance.now() - start);
    telemetry.recordNetworkCall(path, durationMs, res.status, traceId);
    if (!res.ok) {
      const err = await res.text();
      throw new Error(`API ${res.status}: ${err}`);
    }
    return res.json();
  } catch (err: any) {
    const durationMs = Math.round(performance.now() - start);
    telemetry.recordNetworkCall(path, durationMs, 0, traceId);
    throw err;
  }
}

function post<T>(path: string, body?: any): Promise<T> {
  const headers: Record<string, string> = {};
  if (body) headers['Content-Type'] = 'application/json';
  return request<T>(path, {
    method: 'POST',
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
}

function get<T>(path: string): Promise<T> { return request<T>(path); }

export const api = {
  // Auth
  login: (u: string, p: string) => {
    const form = new URLSearchParams();
    form.append('username', u);
    form.append('password', p);
    return request<{ access_token: string; token_type: string }>('/auth/token', {
      method: 'POST', body: form,
    });
  },
  me: () => get<{ id: number; username: string; role: string }>('/auth/me'),

  // Status
  status: () => get<any>('/status'),
  health: () => get<any>('/health'),
  healthDetailed: () => get<any>('/health/detailed'),
  metrics: () => get<any>('/metrics'),

  // Cameras
  cameras: () => get<any[]>('/cameras'),
  camera: (id: number) => get<any>(`/cameras/${id}`),
  createCamera: (d: any) => post<any>('/cameras', d),
  updateCamera: (id: number, d: any) => request<any>(`/cameras/${id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(d) }),
  deleteCamera: (id: number) => request<any>(`/cameras/${id}`, { method: 'DELETE' }),
  connectCamera: (id: number) => post<any>(`/cameras/${id}/connect`),
  disconnectCamera: (id: number) => post<any>(`/cameras/${id}/disconnect`),
  startCameraStream: (id: number) => post<any>(`/cameras/${id}/stream/start`),
  stopCameraStream: (id: number) => post<any>(`/cameras/${id}/stream/stop`),
  toggleCameraPower: (id: number, power: boolean) => post<any>(`/cameras/${id}/power?power=${power}`),
  cameraHealth: (id: number) => get<any[]>(`/cameras/${id}/health`),
  cameraBrands: () => get<any[]>('/cameras/brands'),
  networkInfo: () => get<any>('/cameras/network-info'),
  discoverCameras: (range?: string) => post<any[]>('/cameras/discover', { ip_range: range || '', start: 1, end: 254 }),
  smartProbe: (ip: string, user?: string, pass?: string, brand?: string) => post<any>('/cameras/smart-probe', { ip, username: user || 'admin', password: pass || '', brand: brand || 'auto' }),
  testStream: (url: string, brand?: string, user?: string, pass?: string) => post<any>('/cameras/test-stream', { stream_url: url, brand: brand || 'generic', username: user || 'admin', password: pass || '' }),
  uploadPhoneFrame: (camId: string, imageB64: string) => post<any>(`/cameras/phone-stream/${camId}/frame`, { image: imageB64 }),
  startAllPipelines: () => post<any>('/cameras/pipeline/start-all'),
  powerOffAllHardware: () => post<any>('/cameras/hardware/power-off-all'),
  ptzCommand: (id: number, direction: string, speed: number = 0.5) => post<any>(`/cameras/${id}/ptz`, { direction, speed }),
  ptzPresets: (id: number) => get<any>(`/cameras/${id}/ptz/presets`),
  ptzGoto: (id: number, preset: string) => post<any>(`/cameras/${id}/ptz/goto`, { preset }),

  // Zones
  zones: (cameraId?: number) => get<any[]>(cameraId ? `/zones?camera_id=${cameraId}` : '/zones'),
  createZone: (d: any) => post<any>('/zones', d),
  deleteZone: (id: number) => request<any>(`/zones/${id}`, { method: 'DELETE' }),

  // Incidents
  incidents: (status?: string, severity?: string) => {
    let q = '/incidents?';
    if (status) q += `status=${status}&`;
    if (severity) q += `severity=${severity}&`;
    return get<any[]>(q);
  },
  incident: (id: number) => get<any>(`/incidents/${id}`),
  acknowledgeIncident: (id: number) => post<any>(`/incidents/${id}/acknowledge`),
  escalateIncident: (id: number) => post<any>(`/incidents/${id}/escalate`),
  dismissIncident: (id: number) => post<any>(`/incidents/${id}/dismiss`),
  closeIncident: (id: number) => post<any>(`/incidents/${id}/close`),
  incidentTimeline: (id: number) => get<any>(`/incidents/${id}/timeline`),

  // Alerts
  alerts: (status?: string) => get<any[]>(status ? `/incidents/alerts?status=${status}` : '/incidents/alerts'),

  // Evidence
  evidence: (incidentId: number) => get<any[]>(`/evidence/${incidentId}`),
  verifyEvidence: (id: number) => get<any>(`/evidence/verify/${id}`),
  verifyChain: (incidentId: number) => get<any>(`/evidence/chain/${incidentId}`),
  certificate: (id: number) => get<any>(`/evidence/certificate/${id}`),

  // Events
  events: (cameraId?: number, limit?: number) => {
    let q = '/events?';
    if (cameraId) q += `camera_id=${cameraId}&`;
    if (limit) q += `limit=${limit}&`;
    return get<any[]>(q);
  },

  // Audit
  audit: (limit?: number) => get<any[]>(`/audit?limit=${limit || 100}`),
  auditLogs: (limit?: number) => get<any[]>(`/audit?limit=${limit || 100}`),
  verifyAudit: () => get<any>('/audit/verify'),
  verifyAuditChain: () => get<any>('/audit/verify'),

  // Sync
  syncStatus: () => get<any>('/sync/status'),

  // Demo
  demoScenarios: () => get<any[]>('/demo/scenarios'),
  demoSeed: (scenario?: string) => post<any>(`/demo/seed?scenario=${scenario || 'intrusion'}`),
  demoSeedAll: () => post<any>('/demo/seed/all'),

  // ANPR Checkpost Terminal
  anprPlates: (status?: string, bop?: string, search?: string) => {
    let q = '/anpr/plates?';
    if (status) q += `status=${status}&`;
    if (bop) q += `bop=${encodeURIComponent(bop)}&`;
    if (search) q += `search=${encodeURIComponent(search)}&`;
    return get<any[]>(q);
  },
  anprScan: (d: any) => post<any>('/anpr/scan', d),
  anprScanFile: async (file: File, bop: string = 'BOP-01 Road Checkpost') => {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('bop', bop);
    const token = getAuthToken();
    const headers: Record<string, string> = {};
    if (token) headers['Authorization'] = `Bearer ${token}`;
    const res = await fetch(`${BASE}/anpr/scan-file`, {
      method: 'POST',
      headers,
      body: formData,
    });
    if (!res.ok) {
      const err = await res.text();
      throw new Error(`ANPR Scan Error (${res.status}): ${err}`);
    }
    return res.json();
  },
  anprWatchlist: () => get<any[]>('/anpr/watchlist'),
  addAnprWatchlist: (d: any) => post<any>('/anpr/watchlist', d),
  toggleBarrier: () => post<any>('/anpr/barrier/toggle'),
  anprStats: () => get<any>('/anpr/stats'),

  // FRS Facial Recognition Watchlist
  frsWatchlist: (threatLevel?: string) => get<any[]>(threatLevel ? `/frs/watchlist?threat_level=${threatLevel}` : '/frs/watchlist'),
  enrollSuspect: (d: any) => post<any>('/frs/watchlist', d),
  frsMatches: () => get<any[]>('/frs/matches'),
  frsVerifyProbe: async (file: File) => {
    const formData = new FormData();
    formData.append('file', file);
    const token = getAuthToken();
    const headers: Record<string, string> = {};
    if (token) headers['Authorization'] = `Bearer ${token}`;
    const res = await fetch(`${BASE}/frs/verify-probe`, {
      method: 'POST',
      headers,
      body: formData,
    });
    if (!res.ok) {
      const err = await res.text();
      throw new Error(`FRS Probe Error (${res.status}): ${err}`);
    }
    return res.json();
  },
  verifyFaceMatch: (matchId: number, confirm: boolean) => post<any>(`/frs/matches/${matchId}/verify?confirm=${confirm}`),
  frsStats: () => get<any>('/frs/stats'),

  // Sample Video Fixtures (Air-Gap Verification)
  listSamples: () => get<any[]>('/media/samples/list'),
  importSample: async (filename: string) => {
    const formData = new FormData();
    formData.append('filename', filename);
    const token = getAuthToken();
    const headers: Record<string, string> = {};
    if (token) headers['Authorization'] = `Bearer ${token}`;
    const res = await fetch(`${BASE}/media/samples/import`, {
      method: 'POST',
      headers,
      body: formData,
    });
    if (!res.ok) {
      const err = await res.text();
      throw new Error(`Import Sample Error (${res.status}): ${err}`);
    }
    return res.json();
  },

  // QRT Tactical Dispatch Center
  qrtTeams: () => get<any[]>('/qrt/teams'),
  dispatchQrt: (d: any) => post<any>('/qrt/dispatch', d),
  updateQrtStatus: (d: any) => post<any>('/qrt/status', d),
  qrtLogs: () => get<any[]>('/qrt/logs'),
  broadcastRadio: (d: any) => post<any>('/qrt/radio/broadcast', d),

  // Real Camera Hardware Verification
  testCamera: (id: number) => post<any>(`/cameras/${id}/test`),

  // Real Video Upload & Media Assets
  uploadMedia: async (file: File) => {
    const token = getAuthToken();
    const formData = new FormData();
    formData.append('file', file);
    const headers: Record<string, string> = {};
    if (token) headers['Authorization'] = `Bearer ${token}`;
    headers['X-Trace-ID'] = telemetry.generateTraceId();

    const res = await fetch(`${BASE}/media/upload`, {
      method: 'POST',
      headers,
      body: formData,
    });
    if (!res.ok) {
      const err = await res.text();
      throw new Error(`Upload Failed (${res.status}): ${err}`);
    }
    return res.json();
  },
  mediaAssets: () => get<any[]>('/media'),
  mediaAsset: (id: number) => get<any>(`/media/${id}`),
  deleteMedia: (id: number) => request<any>(`/media/${id}`, { method: 'DELETE' }),

  createAnalysisJob: (d: {
    source_type: string;
    source_id?: number;
    source_url?: string;
    detector_model?: string;
    confidence_threshold?: number;
    zone_ids?: number[];
    enable_anpr?: boolean;
    enable_tracking?: boolean;
    enable_behavior?: boolean;
    enable_night_mode?: boolean;
    enable_face?: boolean;
  }) => post<any>('/analysis/jobs', d),
  analysisJobs: () => get<any[]>('/analysis/jobs'),
  analysisJob: (id: number | string) => get<any>(`/analysis/jobs/${id}`),
  analysisResults: (id: number | string) => get<any>(`/analysis/jobs/${id}/results`),
  analysisJobTracks: (id: number | string) => get<any>(`/analysis/jobs/${id}/tracks`),
  cancelAnalysisJob: (id: number | string) => post<any>(`/analysis/jobs/${id}/cancel`),
  analysisJobReportUrl: (id: number | string, format: 'json' | 'pdf' = 'json') => `${BASE}/analysis/jobs/${id}/report?format=${format}`,

  // System Readiness (Task 4.2 & 5.4)
  systemReadiness: () => get<any>('/system/readiness'),

  // Real ANPR Plate Reads (Task 2.1 & 5.2)
  searchPlates: (q?: string, jobId?: number) => {
    let path = '/plates?';
    if (q) path += `q=${encodeURIComponent(q)}&`;
    if (jobId) path += `job_id=${jobId}&`;
    return get<any[]>(path);
  },

  // Face Watchlist & Intelligence (Task 3.2 & 5.3)
  watchlist: () => get<any[]>('/watchlist'),
  enrollWatchlist: async (name: string, notes: string, file: File) => {
    const token = getAuthToken();
    const formData = new FormData();
    formData.append('name', name);
    formData.append('notes', notes);
    formData.append('file', file);
    const headers: Record<string, string> = {};
    if (token) headers['Authorization'] = `Bearer ${token}`;
    headers['X-Trace-ID'] = telemetry.generateTraceId();

    const res = await fetch(`${BASE}/watchlist/enroll`, {
      method: 'POST',
      headers,
      body: formData,
    });
    if (!res.ok) {
      const err = await res.text();
      throw new Error(`Enrollment Failed (${res.status}): ${err}`);
    }
    return res.json();
  },
  deleteWatchlist: (id: number) => request<any>(`/watchlist/${id}`, { method: 'DELETE' }),
  watchlistMatches: () => get<any[]>('/watchlist/matches/recent'),

  // Evidence Verification (Task 4.1)
  verifyEvidenceHash: (id: number) => get<any>(`/evidence/${id}/verify`),

  // Map Situational Awareness (Phase D)
  getMapData: () => get<any>('/map'),
  patchCamera: (id: number, data: any) => request<any>(`/cameras/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  }),
};

