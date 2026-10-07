/**
 * IBVAP Tactical Share Links & Deep Linking Utility
 * SIH PS-26187 | SSB, Ministry of Home Affairs
 *
 * Serializes and restores tactical application state (view, camera, job, incident,
 * map center & zoom) to and from window.location.search.
 */

export interface TacticalShareParams {
  view?: string;
  cameraId?: number | null;
  jobId?: number | string | null;
  incidentId?: number | null;
  lat?: number | null;
  lng?: number | null;
  zoom?: number | null;
}

/**
 * Serializes parameters to a standard URL query string.
 */
export function serializeTacticalParams(params: TacticalShareParams): string {
  const searchParams = new URLSearchParams();

  if (params.view) searchParams.set('view', params.view);
  if (params.cameraId !== null && params.cameraId !== undefined) {
    searchParams.set('camera_id', String(params.cameraId));
  }
  if (params.jobId !== null && params.jobId !== undefined) {
    searchParams.set('job_id', String(params.jobId));
  }
  if (params.incidentId !== null && params.incidentId !== undefined) {
    searchParams.set('incident_id', String(params.incidentId));
  }
  if (params.lat !== null && params.lat !== undefined) {
    searchParams.set('lat', params.lat.toFixed(4));
  }
  if (params.lng !== null && params.lng !== undefined) {
    searchParams.set('lng', params.lng.toFixed(4));
  }
  if (params.zoom !== null && params.zoom !== undefined) {
    searchParams.set('zoom', String(params.zoom));
  }

  return searchParams.toString();
}

/**
 * Parses URL search string or full URL into typed tactical parameters.
 */
export function parseTacticalParams(searchOrUrl?: string | null): TacticalShareParams {
  let search = String(searchOrUrl || '');
  if (search.includes('?')) {
    search = search.substring(search.indexOf('?') + 1);
  }

  const p = new URLSearchParams(search);
  const result: TacticalShareParams = {};

  const view = p.get('view');
  if (view) result.view = view;

  const camId = p.get('camera_id');
  if (camId) {
    const parsed = parseInt(camId, 10);
    if (!isNaN(parsed)) result.cameraId = parsed;
  }

  const jId = p.get('job_id');
  if (jId) result.jobId = jId;

  const incId = p.get('incident_id');
  if (incId) {
    const parsed = parseInt(incId, 10);
    if (!isNaN(parsed)) result.incidentId = parsed;
  }

  const lat = p.get('lat');
  if (lat) {
    const parsed = parseFloat(lat);
    if (!isNaN(parsed)) result.lat = parsed;
  }

  const lng = p.get('lng');
  if (lng) {
    const parsed = parseFloat(lng);
    if (!isNaN(parsed)) result.lng = parsed;
  }

  const zoom = p.get('zoom');
  if (zoom) {
    const parsed = parseInt(zoom, 10);
    if (!isNaN(parsed)) result.zoom = parsed;
  }

  return result;
}

/**
 * Updates browser history state without triggering page reload.
 */
export function updateBrowserUrl(params: TacticalShareParams): void {
  if (typeof window === 'undefined' || !window.history) return;
  const qs = serializeTacticalParams(params);
  const newUrl = qs ? `${window.location.pathname}?${qs}` : window.location.pathname;
  window.history.replaceState(null, '', newUrl);
}

/**
 * Copies the current tactical share URL to operator clipboard.
 */
export async function copyShareLink(params?: TacticalShareParams): Promise<boolean> {
  if (typeof window === 'undefined') return false;
  let url = window.location.href;
  if (params) {
    const qs = serializeTacticalParams(params);
    url = `${window.location.origin}${window.location.pathname}${qs ? '?' + qs : ''}`;
  }

  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(url);
      return true;
    }
  } catch (e) {
    console.warn('Clipboard write failed:', e);
  }
  return false;
}
