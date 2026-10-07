import { create } from './zustand';
import { api } from '../api';
import type { Camera, Incident, Alert, DemoScenario } from '../types';
import { playTacticalTone } from '../utils/audio';

export type Page =
  | 'dashboard'
  | 'map'
  | 'cameras'
  | 'anpr'
  | 'frs'
  | 'qrt'
  | 'thermal'
  | 'incidents'
  | 'incident-detail'
  | 'evidence'
  | 'audit'
  | 'health'
  | 'demo'
  | 'settings'
  | 'media';

export interface DetectionToast {
  id: string;
  title: string;
  subtitle: string;
  severity: string;
  time: string;
  incidentId?: number;
}

export interface TacticalState {
  page: Page;
  cameras: Camera[];
  incidents: Incident[];
  alerts: Alert[];
  status: any;
  scenarios: DemoScenario[];
  selectedIncident: Incident | null;
  selectedBop: string | null;
  isSearchOpen: boolean;
  isMuted: boolean;
  wsStatus: 'connected' | 'disconnected' | 'connecting';
  toasts: DetectionToast[];
  showLoginModal: boolean;
  currentUser: { username: string; role: string } | null;
  busy: boolean;
  demoMsg: string;

  // Actions
  setPage: (page: Page) => void;
  setSelectedIncident: (inc: Incident | null) => void;
  setSelectedBop: (bop: string | null) => void;
  setIsSearchOpen: (open: boolean) => void;
  setShowLoginModal: (show: boolean) => void;
  setCurrentUser: (user: { username: string; role: string } | null) => void;
  setWsStatus: (status: 'connected' | 'disconnected' | 'connecting') => void;
  toggleMute: () => void;
  addToast: (t: Omit<DetectionToast, 'id' | 'time'>) => void;
  removeToast: (id: string) => void;
  fetchInitialData: () => Promise<void>;
  openIncident: (id: number) => Promise<void>;
  runDemoScenario: (scenario: string) => Promise<void>;
  runAllScenarios: () => Promise<void>;
}

export const useTacticalStore = create<TacticalState>((set, get) => ({
  page: 'dashboard',
  cameras: [],
  incidents: [],
  alerts: [],
  status: null,
  scenarios: [],
  selectedIncident: null,
  selectedBop: null,
  isSearchOpen: false,
  isMuted: typeof localStorage !== 'undefined' ? localStorage.getItem('ibvap_muted') === 'true' : false,
  wsStatus: 'disconnected',
  toasts: [],
  showLoginModal: typeof localStorage !== 'undefined' ? !(localStorage.getItem('ibvap_token') || localStorage.getItem('token')) : false,
  currentUser: null,
  busy: false,
  demoMsg: '',

  setPage: (page: Page) => {
    playTacticalTone('click');
    set({ page });
  },

  setSelectedIncident: (inc: Incident | null) => set({ selectedIncident: inc }),
  setSelectedBop: (bop: string | null) => {
    playTacticalTone('click');
    set({ selectedBop: bop });
  },
  setIsSearchOpen: (open: boolean) => {
    playTacticalTone('click');
    set({ isSearchOpen: open });
  },
  setShowLoginModal: (show: boolean) => set({ showLoginModal: show }),
  setCurrentUser: (user) => set({ currentUser: user }),
  setWsStatus: (status) => set({ wsStatus: status }),

  toggleMute: () => {
    const next = !get().isMuted;
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem('ibvap_muted', String(next));
    }
    set({ isMuted: next });
    if (!next) playTacticalTone('verify');
  },

  addToast: (t) => {
    const id = Math.random().toString(36).substring(2, 9);
    const time = new Date().toLocaleTimeString();
    const item: DetectionToast = { ...t, id, time };
    set({ toasts: [item, ...get().toasts.slice(0, 3)] });
    if (!get().isMuted) playTacticalTone('alert');

    if (typeof window !== 'undefined' && 'Notification' in window && Notification.permission === 'granted') {
      try {
        new Notification(t.title, { body: `${t.subtitle} [${t.severity}]` });
      } catch {}
    }
  },

  removeToast: (id: string) => {
    set({ toasts: get().toasts.filter((x) => x.id !== id) });
  },

  fetchInitialData: async () => {
    try {
      const [c, i, a, s, sc] = await Promise.all([
        api.cameras().catch(() => []),
        api.incidents().catch(() => []),
        api.alerts().catch(() => []),
        api.status().catch(() => null),
        api.demoScenarios().catch(() => []),
      ]);
      set({
        cameras: Array.isArray(c) ? c : [],
        incidents: Array.isArray(i) ? i : [],
        alerts: Array.isArray(a) ? a : [],
        status: s,
        scenarios: Array.isArray(sc) ? sc : [],
      });

      if (typeof localStorage !== 'undefined' && (localStorage.getItem('ibvap_token') || localStorage.getItem('token'))) {
        api.me().then((u) => set({ currentUser: u })).catch(() => {});
      }
    } catch {
      /* resilient fallback */
    }
  },

  openIncident: async (id: number) => {
    playTacticalTone('click');
    try {
      const inc = await api.incident(id);
      set({ selectedIncident: inc, page: 'incident-detail' });
    } catch (err) {
      console.error('Failed to load incident detail', err);
    }
  },

  runDemoScenario: async (scenario: string) => {
    playTacticalTone('click');
    set({ busy: true, demoMsg: `Triggering tactical scenario: ${scenario}...` });
    try {
      const result = await api.demoSeed(scenario);
      set({
        demoMsg: `Tactical incident logged: ${result.incident_type || scenario} (Threat: ${result.threat_score})`,
      });
      get().addToast({
        title: `WAR GAMING // ${result.incident_type || scenario}`,
        subtitle: `Sector ${result.bop || 'BOP-01'} • Threat Score: ${result.threat_score || 80} • Injected successfully`,
        severity: result.severity || 'HIGH',
        incidentId: result.id,
      });
      await get().fetchInitialData();
      setTimeout(() => set({ demoMsg: '' }), 5000);
    } catch (e: any) {
      set({ demoMsg: `Simulation notice: ${e?.message || e}` });
    } finally {
      set({ busy: false });
    }
  },

  runAllScenarios: async () => {
    playTacticalTone('click');
    set({ busy: true, demoMsg: 'Running full sector war game exercise across all 7 BOPs...' });
    try {
      const results = await api.demoSeedAll();
      set({ demoMsg: `Exercise complete: ${results.length} sector threats processed` });
      get().addToast({
        title: 'FULL SECTOR DRILL INITIATED',
        subtitle: `${results.length} multi-node threats simulated across border perimeter`,
        severity: 'CRITICAL',
      });
      await get().fetchInitialData();
      setTimeout(() => set({ demoMsg: '' }), 6000);
    } catch (e: any) {
      set({ demoMsg: `Simulation notice: ${e?.message || e}` });
    } finally {
      set({ busy: false });
    }
  },
}));
