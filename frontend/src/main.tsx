import React, { useEffect, useMemo } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';

import { useTacticalStore } from './store/useTacticalStore';
import { useLiveEvents } from './hooks/useLiveFeed';
import { playTacticalTone } from './utils/audio';
import { telemetry } from './utils/telemetry';

// Initialize frontend telemetry and Web Vitals monitoring
telemetry.init();

import { Sidebar } from './components/Sidebar';
import { TopBar } from './components/TopBar';
import { CommandSearchModal } from './components/CommandSearchModal';
import { TacticalLoginModal } from './components/TacticalLoginModal';

import { AnalyticsDashboard } from './views/AnalyticsDashboard';
import { LiveMonitorView } from './views/LiveMonitorView';
import { IncidentTriageView, IncidentDetailView } from './views/IncidentTriageView';
import { ANPRView } from './views/ANPRView';
import { FRSView } from './views/FRSView';
import { QRTView } from './views/QRTView';
import { ThermalDroneView } from './views/ThermalDroneView';
import { EvidenceView } from './views/EvidenceView';
import { AuditView } from './views/AuditView';
import { HealthView } from './views/HealthView';
import { MediaAnalysisView } from './views/MediaAnalysisView';
import { DemoView } from './views/DemoView';
import { SettingsView } from './views/SettingsView';
import { MapView } from './views/MapView';
import { PatrolMode } from './components/PatrolMode';
import { parseTacticalParams, updateBrowserUrl } from './utils/shareLinks';

/* ─── Main Tactical Application Router ────────────────────────── */
function App() {
  const store = useTacticalStore();
  useLiveEvents();
  const [isPatrolActive, setIsPatrolActive] = React.useState(false);

  useEffect(() => {
    store.fetchInitialData();

    // Deep Share Link Restore (Task 5.1)
    if (typeof window !== 'undefined' && window.location.search) {
      const p = parseTacticalParams(window.location.search);
      if (p.view && ['dashboard', 'map', 'cameras', 'anpr', 'frs', 'qrt', 'thermal', 'incidents', 'incident-detail', 'evidence', 'audit', 'health', 'demo', 'settings', 'media'].includes(p.view)) {
        store.setPage(p.view as any);
      }
      if (p.incidentId) {
        store.openIncident(p.incidentId);
      }
    }
  }, []);

  // Synchronize URLSearchParams on view or incident change (Task 5.1)
  useEffect(() => {
    updateBrowserUrl({
      view: store.page,
      incidentId: store.selectedIncident?.id || null,
    });
  }, [store.page, store.selectedIncident]);

  // Cmd+K / Ctrl+K global spotlight hotkey
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        store.setIsSearchOpen(!store.isSearchOpen);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [store.isSearchOpen]);

  const maxThreatScore = useMemo(
    () => store.incidents.reduce((max, inc) => Math.max(max, inc.threat_score || 0), 0),
    [store.incidents]
  );

  const handleLaunchMission = async (key: string) => {
    playTacticalTone('verify');
    if (key === 'checkpost') {
      store.setPage('anpr');
      store.addToast({
        title: 'MISSION: CHECKPOST INTERCEPT ENGAGED',
        subtitle: 'Indian HSRP Neural ANPR pipeline engaged with barrier lockout',
        severity: 'HIGH',
      });
    } else if (key === 'night_breach') {
      store.setPage('cameras');
      await store.runDemoScenario('intrusion');
      store.addToast({
        title: 'MISSION: NIGHT BREACH DETECTED',
        subtitle: 'Zero-DCE++ low-light perception & virtual fence breach triggered',
        severity: 'CRITICAL',
      });
    } else if (key === 'mac_sentry') {
      store.setPage('cameras');
      store.addToast({
        title: 'MISSION: HARDWARE SENTRY NODE ENGAGED',
        subtitle: 'Live local Mac hardware camera feed active with biometric perception',
        severity: 'LOW',
      });
    } else if (key === 'drone_recon') {
      store.setPage('thermal');
      store.addToast({
        title: 'MISSION: EO/IR THERMAL DRONE PATROL ACTIVE',
        subtitle: 'Multi-spectral thermal aerial scan calibrated for zero-line boundary',
        severity: 'MEDIUM',
      });
    }
  };

  return (
    <div className="app">
      <Sidebar
        page={store.page}
        setPage={store.setPage}
        alerts={store.alerts}
        ws={store.wsStatus}
      />

      <div className="main-area">
        <TopBar
          status={store.status}
          ws={store.wsStatus}
          maxThreat={maxThreatScore}
          isMuted={store.isMuted}
          onToggleMute={store.toggleMute}
          onSync={store.fetchInitialData}
          onTestAlert={() => store.runDemoScenario('intrusion')}
          onOpenSearch={() => store.setIsSearchOpen(true)}
          selectedBop={store.selectedBop}
          onSelectBop={store.setSelectedBop}
          currentUser={store.currentUser}
          onOpenLogin={() => store.setShowLoginModal(true)}
          onTogglePatrol={() => setIsPatrolActive((prev) => !prev)}
          isPatrolActive={isPatrolActive}
          onLaunchMission={handleLaunchMission}
        />

        {!store.status && (
          <div
            className="tactical-offline-banner"
            style={{
              background: 'rgba(255, 42, 85, 0.15)',
              borderBottom: '1px solid #ff2a55',
              color: '#ff2a55',
              fontSize: 11,
              padding: '6px 16px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              fontWeight: 600,
              fontFamily: 'var(--font-mono)',
            }}
          >
            <span>⚠️ OFFLINE RESILIENCE MODE — TELEMETRY DISCONNECTED. OPERATING LOCALLY ON AIR-GAPPED CACHE.</span>
            <button
              className="btn btn-sm btn-secondary"
              onClick={store.fetchInitialData}
              style={{ fontSize: 10, padding: '2px 8px', borderColor: '#ff2a55', color: '#ff2a55' }}
            >
              🔄 Reconnect
            </button>
          </div>
        )}

        <main className="content">
          {store.page === 'dashboard' && <AnalyticsDashboard />}
          {store.page === 'map' && (
            <MapView
              onSelectIncident={store.openIncident}
              onSelectCamera={() => store.setPage('cameras')}
            />
          )}
          {store.page === 'cameras' && (
            <LiveMonitorView
              cameras={store.cameras}
              incidents={store.incidents}
              onRefresh={store.fetchInitialData}
            />
          )}
          {store.page === 'media' && <MediaAnalysisView />}
          {store.page === 'anpr' && <ANPRView />}
          {store.page === 'frs' && <FRSView openInc={store.openIncident} />}
          {store.page === 'qrt' && <QRTView incidents={store.incidents} />}
          {store.page === 'thermal' && <ThermalDroneView cameras={store.cameras} />}
          {store.page === 'incidents' && (
            <IncidentTriageView
              incidents={store.incidents}
              openInc={store.openIncident}
            />
          )}
          {store.page === 'incident-detail' && store.selectedIncident && (
            <IncidentDetailView
              incident={store.selectedIncident}
              onBack={() => store.setPage('incidents')}
              refresh={store.openIncident}
            />
          )}
          {store.page === 'evidence' && <EvidenceView incidents={store.incidents} />}
          {store.page === 'audit' && <AuditView />}
          {store.page === 'health' && <HealthView cameras={store.cameras} />}
          {store.page === 'demo' && (
            <DemoView
              scenarios={store.scenarios}
              runDemo={store.runDemoScenario}
              runAll={store.runAllScenarios}
              busy={store.busy}
              demoMsg={store.demoMsg}
            />
          )}
          {store.page === 'settings' && (
            <SettingsView
              isMuted={store.isMuted}
              onToggleMute={store.toggleMute}
            />
          )}
        </main>
      </div>

      {/* Global Command Search (Cmd+K) */}
      <CommandSearchModal
        isOpen={store.isSearchOpen}
        onClose={() => store.setIsSearchOpen(false)}
        cameras={store.cameras}
        incidents={store.incidents}
        onSelectCamera={() => store.setPage('cameras')}
        onSelectIncident={store.openIncident}
        onNavigatePage={store.setPage}
      />

      {/* Tactical Authentication Modal */}
      {store.showLoginModal && (
        <TacticalLoginModal
          onSuccess={(user) => {
            store.setCurrentUser(user);
            store.setShowLoginModal(false);
            store.fetchInitialData();
            store.addToast({
              title: 'SECURITY CLEARANCE GRANTED',
              subtitle: `Authenticated as ${user.role} (${user.username})`,
              severity: 'LOW',
            });
          }}
          onClose={() => store.setShowLoginModal(false)}
        />
      )}

      {/* Real-time Detection HUD Toasts */}
      <div className="tactical-toast-container">
        {store.toasts.map((t) => (
          <div key={t.id} className={`tactical-toast toast-${(t.severity || 'high').toLowerCase()}`}>
            <div className="toast-header">
              <span className="toast-pulse-dot" />
              <span className="toast-tag">{t.severity || 'HIGH'} ALERT</span>
              <span className="toast-time">{t.time}</span>
              <button
                className="toast-close"
                onClick={() => store.removeToast(t.id)}
                title="Dismiss"
              >
                ✕
              </button>
            </div>
            <div className="toast-title">{t.title}</div>
            <div className="toast-sub">{t.subtitle}</div>
            {t.incidentId && (
              <button
                className="toast-action-btn"
                onClick={() => {
                  store.removeToast(t.id);
                  store.openIncident(t.incidentId!);
                }}
              >
                Triage Incident ➔
              </button>
            )}
          </div>
        ))}
      </div>

      {/* Scripted Tactical Patrol Mode Tour (Task 6) */}
      <PatrolMode
        isActive={isPatrolActive}
        onClose={() => setIsPatrolActive(false)}
        onNavigate={store.setPage}
      />
    </div>
  );
}

/* ─── Production Error Boundary ───────────────────────────────── */
class ErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { hasError: boolean; error: any }
> {
  constructor(props: any) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: any) {
    return { hasError: true, error };
  }

  componentDidCatch(error: any, errorInfo: any) {
    console.error('Tactical Console Exception:', error, errorInfo);
    telemetry.recordEvent({
      timestamp: new Date().toISOString(),
      type: 'error',
      message: error?.message || String(error),
      traceId: telemetry.getTraceId(),
      metadata: {
        componentStack: errorInfo?.componentStack,
        stack: error?.stack,
      },
    });
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{ height: '100vh', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', background: '#02060c', color: '#e2effc', fontFamily: 'monospace', padding: 24, textAlign: 'center' }}>
          <div style={{ color: '#ff2a55', fontSize: 24, fontWeight: 'bold', marginBottom: 12 }}>
            ⚠️ TACTICAL C4ISR CONSOLE RECOVERY
          </div>
          <p style={{ maxWidth: 500, color: 'var(--text-secondary)', marginBottom: 20 }}>
            {String(this.state.error?.message || this.state.error || 'A state synchronization issue occurred.')}
          </p>
          <button
            style={{ padding: '10px 24px', background: '#00f0ff', color: '#02060c', border: 'none', borderRadius: 4, fontWeight: 'bold', cursor: 'pointer', letterSpacing: 1 }}
            onClick={() => {
              localStorage.removeItem('ibvap_token');
              localStorage.removeItem('token');
              window.location.reload();
            }}
          >
            ↻ RECOVER & RECONNECT CONSOLE
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

/* ─── Mount React Application ─────────────────────────────────── */
const root = document.getElementById('root');
if (root) {
  createRoot(root).render(
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  );
}
