import React, {useState, useEffect, useCallback, useRef} from 'react';
import {createRoot} from 'react-dom/client';
import {api, ApiError, passwordLogin, setCsrf} from './api';
import {
  type RecordData,
  type Principal,
  type Envelope,
  type Capability,
  type RouteInfo,
  isObjectRecord,
} from './types';
import {parseRoute, navigate, Link, PRIMARY_DESTINATIONS} from './nav';
import {Value, ErrorBox, AttentionBanner, StateView} from './primitives';
import {ActionPanel} from './command';
import {Filters} from './forms';
import {OverviewPage} from './pages/OverviewPage';
import {TradingPage} from './pages/TradingPage';
import {SignalsPage} from './pages/SignalsPage';
import {ActivityPage} from './pages/ActivityPage';
import {SettingsPage} from './pages/SettingsPage';
import './style.css';

const TABS: Record<string, {label: string; path: string}[]> = {
  overview: [
    {label: 'Overview', path: '/overview'},
    {label: 'Account & Performance', path: '/account'},
  ],
  trading: [
    {label: 'Positions', path: '/positions'},
    {label: 'Orders & Intents', path: '/orders'},
    {label: 'Fills & Ledger', path: '/orders?view=fills'},
    {label: 'Protection', path: '/protection'},
    {label: 'New Trade', path: '/trading'},
  ],
  signals: [
    {label: 'Signals Inbox', path: '/signals'},
    {label: 'Dispatches & Trace', path: '/signals/dispatches'},
  ],
  activity: [
    {label: 'Audit Trail', path: '/audit'},
    {label: 'Command Jobs', path: '/audit?view=jobs'},
    {label: 'Approvals', path: '/audit?view=approvals'},
    {label: 'Management', path: '/management'},
    {label: 'Notifications', path: '/notifications'},
  ],
  settings: [
    {label: 'Configuration', path: '/config'},
    {label: 'Safety & Recovery', path: '/recovery'},
    {label: 'Services & Attestation', path: '/services'},
    {label: 'Releases', path: '/services?view=releases'},
    {label: 'Backups', path: '/backups'},
    {label: 'Access & Sessions', path: '/access'},
  ],
};

function Auth({onAuthenticated}: {onAuthenticated: () => void}) {
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    const submitted = password;
    setPassword('');
    try {
      await passwordLogin(submitted);
      onAuthenticated();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="auth-wrapper">
      <div className="auth-card">
        <span className="brand-badge" style={{marginBottom: '12px'}}>FATTY CONSOLE</span>
        <h1 className="auth-title">Evidence Before Action</h1>
        <p className="text-secondary text-sm" style={{marginBottom: '20px'}}>
          Sign in with the shared console password to access scoped observations and management jobs.
        </p>
        <ErrorBox error={error} />
        <form onSubmit={submit} data-testid="password-auth">
          <label className="form-group">
            <span className="form-label">Console password</span>
            <input
              data-testid="console-password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              disabled={busy}
              placeholder="Enter operator password"
            />
          </label>
          <button
            type="submit"
            className="btn btn-primary"
            style={{width: '100%', marginTop: '8px'}}
            data-testid="password-login"
            disabled={busy}
          >
            {busy ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
        <p className="text-muted text-xs" style={{marginTop: '20px', textAlign: 'center'}}>
          Use the authorized HTTPS origin or localhost SSH tunnel. Passwords are cleared from memory immediately.
        </p>
      </div>
    </main>
  );
}

function App() {
  const [principal, setPrincipal] = useState<Principal | null>(null);
  const [checking, setChecking] = useState(true);
  const [currentRoute, setCurrentRoute] = useState<RouteInfo>(() =>
    parseRoute(location.pathname, location.search)
  );
  const [envelope, setEnvelope] = useState<Envelope | null>(null);
  const [safety, setSafety] = useState<Envelope | null>(null);
  const [caps, setCaps] = useState<Capability[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);
  const [cursor, setCursor] = useState(() => new URLSearchParams(location.search).get('cursor') || '');
  const [filters, setFilters] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      [...new URLSearchParams(location.search)].filter(
        ([key]) => !['view', 'cursor'].includes(key)
      )
    )
  );
  const [refresh, setRefresh] = useState(0);
  const [connection, setConnection] = useState('connecting');
  const [mobileOpen, setMobileOpen] = useState(false);
  const [theme, setTheme] = useState(() => localStorage.getItem('console.theme') || 'light');
  const [timezone, setTimezone] = useState(() => localStorage.getItem('console.timezone') || 'UTC');

  // Command modal state
  const [actionModalOpen, setActionModalOpen] = useState(false);
  const [actionModalAction, setActionModalAction] = useState('');

  const eventCursor = useRef<string>('');
  const liveRefresh = useRef<number | undefined>(undefined);

  const reload = useCallback(() => setRefresh((r) => r + 1), []);

  async function checkSession() {
    setChecking(true);
    try {
      const result = await api<RecordData>('/session');
      if (isObjectRecord(result.principal)) {
        const p = result.principal as unknown as Principal;
        setCsrf(String(result.csrf_token || p.csrf_token));
        setPrincipal(p);
        setError(null);
      } else {
        setPrincipal(null);
      }
    } catch (e) {
      setPrincipal(null);
      if (!(e instanceof ApiError && e.status === 401)) {
        setError(e);
      }
    } finally {
      setChecking(false);
    }
  }

  // Routing listener
  useEffect(() => {
    void checkSession();
    const handlePopState = () => {
      const nextRoute = parseRoute(location.pathname, location.search);
      const q = new URLSearchParams(location.search);
      setCurrentRoute(nextRoute);
      setCursor(q.get('cursor') || '');
      setFilters(
        Object.fromEntries(
          [...q].filter(([key]) => !['view', 'cursor'].includes(key))
        )
      );
      setEnvelope(null);
      setMobileOpen(false);
      document.getElementById('content')?.focus();
    };
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  // Theme & Timezone sync
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem('console.theme', theme);
  }, [theme]);

  useEffect(() => {
    document.documentElement.dataset.timezone = timezone;
    localStorage.setItem('console.timezone', timezone);
  }, [timezone]);

  // Session events
  useEffect(() => {
    const handleSessionChanged = (event: Event) => {
      const result = (event as CustomEvent<RecordData>).detail;
      if (isObjectRecord(result.principal)) {
        setPrincipal(result.principal as unknown as Principal);
      }
    };
    const handleSessionExpired = () => {
      setPrincipal(null);
      setCsrf('');
      setEnvelope(null);
      setSafety(null);
      setCaps([]);
      eventCursor.current = '';
      setConnection('session expired');
      setError(
        new Error(
          'Session expired or was revoked. Sign in again; existing jobs retain their durable outcome.'
        )
      );
    };
    window.addEventListener('console-session', handleSessionChanged);
    window.addEventListener('console-session-expired', handleSessionExpired);
    return () => {
      window.removeEventListener('console-session', handleSessionChanged);
      window.removeEventListener('console-session-expired', handleSessionExpired);
    };
  }, []);

  // Fetch section data
  useEffect(() => {
    if (!principal) return;
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    const q = new URLSearchParams({
      ...Object.fromEntries(Object.entries(filters).filter(([, v]) => v)),
      limit: '25',
      ...(cursor ? {cursor} : {}),
    });
    const path = `/${currentRoute.readSection}${
      currentRoute.resourceId ? '/' + encodeURIComponent(currentRoute.resourceId) : ''
    }?${q}`;

    api<Envelope>(path, undefined, {}, controller.signal)
      .then((r) => {
        if (controller.signal.aborted) return;
        setEnvelope(r);
        if (!eventCursor.current) {
          eventCursor.current = String(r.event_cursor ?? '');
        }
      })
      .catch((e) => {
        if (controller.signal.aborted || e.name === 'AbortError') return;
        setError(e);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });

    return () => controller.abort();
  }, [principal, currentRoute.readSection, currentRoute.resourceId, filters, cursor, refresh]);

  // Fetch safety & capabilities
  useEffect(() => {
    if (!principal) return;
    const controller = new AbortController();
    void api<Envelope>('/overview', undefined, {}, controller.signal)
      .then((r) => {
        if (!controller.signal.aborted) setSafety(r);
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e);
      });

    void api<Envelope>('/actions', undefined, {}, controller.signal)
      .then((r) => {
        if (controller.signal.aborted) return;
        const catalog =
          isObjectRecord(r.data) && Array.isArray(r.data.items) ? r.data.items : [];
        setCaps(catalog as Capability[]);
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e);
      });

    return () => controller.abort();
  }, [principal, refresh]);

  // SSE event stream
  useEffect(() => {
    if (!principal) return;
    let source: EventSource | null = null;
    let stopped = false;
    let reconnectTimer: number | undefined;
    let delay = 1000;
    let lastHeartbeat = Date.now();

    function triggerInvalidation() {
      clearTimeout(liveRefresh.current);
      liveRefresh.current = window.setTimeout(reload, 350);
    }

    async function connectSSE() {
      if (!eventCursor.current) {
        try {
          const snapshot = await api<Envelope>('/overview');
          setSafety(snapshot);
          eventCursor.current = String(snapshot.event_cursor ?? '');
        } catch (e) {
          setError(e);
          setConnection('snapshot unavailable');
          if (!stopped) reconnectTimer = window.setTimeout(connectSSE, delay);
          return;
        }
      }
      if (stopped) return;
      source = new EventSource(
        `/api/v1/events?cursor=${encodeURIComponent(eventCursor.current)}`
      );
      source.onopen = () => {
        setConnection('live');
        lastHeartbeat = Date.now();
        delay = 1000;
      };
      const onEvent = (e: MessageEvent) => {
        lastHeartbeat = Date.now();
        if (e.lastEventId && /^\d+$/.test(e.lastEventId)) {
          if (
            eventCursor.current &&
            BigInt(e.lastEventId) <= BigInt(eventCursor.current)
          ) {
            return;
          }
          eventCursor.current = e.lastEventId;
        }
        triggerInvalidation();
      };
      source.onmessage = onEvent;
      source.addEventListener('change', onEvent as EventListener);
      source.addEventListener('heartbeat', () => {
        lastHeartbeat = Date.now();
        setConnection('live');
      });
      const resync = () => {
        eventCursor.current = '';
        setEnvelope(null);
        setSafety(null);
        setCaps([]);
        setConnection('resynchronizing');
        triggerInvalidation();
        source?.close();
        if (!stopped) reconnectTimer = window.setTimeout(connectSSE, 1000);
      };
      source.addEventListener('resync', resync);
      source.addEventListener('resync_required', resync);
      source.addEventListener('session_revoked', () => {
        stopped = true;
        source?.close();
        window.dispatchEvent(new Event('console-session-expired'));
      });
      source.onerror = () => {
        setConnection('disconnected');
        source?.close();
        void api('/session').catch((e) => {
          if (!(e instanceof ApiError && e.status === 401)) setError(e);
        });
        if (!stopped) {
          reconnectTimer = window.setTimeout(connectSSE, delay + Math.random() * 500);
        }
        delay = Math.min(delay * 2, 30000);
      };
    }

    void connectSSE();

    const watchdog = setInterval(() => {
      if (Date.now() - lastHeartbeat > 45000) {
        setConnection('stale');
        source?.close();
        lastHeartbeat = Date.now();
        if (!stopped) {
          clearTimeout(reconnectTimer);
          reconnectTimer = window.setTimeout(connectSSE, delay);
        }
      }
    }, 5000);

    return () => {
      stopped = true;
      source?.close();
      clearTimeout(reconnectTimer);
      clearInterval(watchdog);
      clearTimeout(liveRefresh.current);
    };
  }, [principal, reload]);

  if (checking && !principal) {
    return (
      <main className="auth-wrapper">
        <p role="status" className="text-secondary text-sm">Checking secure session…</p>
      </main>
    );
  }

  if (!principal) {
    return (
      <>
        <ErrorBox error={error} />
        <Auth onAuthenticated={() => void checkSession()} />
      </>
    );
  }

  const safetyData = isObjectRecord(safety?.data) ? safety.data : {};
  const latches = Array.isArray(safetyData.latches) ? safetyData.latches : [];
  const currentTabs = TABS[currentRoute.destination] || [];
  const scopeData = envelope?.scope || safety?.scope || {};

  const handleOpenAction = (action: string) => {
    setActionModalAction(action);
    setActionModalOpen(true);
  };

  const showFilters =
    !currentRoute.resourceId &&
    ['signals', 'orders', 'fills', 'audit', 'notifications'].includes(currentRoute.readSection);

  return (
    <div className="app-container">
      {/* Sidebar */}
      <aside className={`app-sidebar ${mobileOpen ? 'open' : ''}`} aria-label="Main sidebar navigation">
        <div className="brand-section">
          <Link to="/overview" className="brand-link">
            <span className="brand-badge">FATTY</span>
            <span>CONSOLE</span>
          </Link>
        </div>

        <nav className="nav-menu" aria-label="Main navigation">
          {PRIMARY_DESTINATIONS.map((dest) => {
            const isActive = currentRoute.destination === dest.id;
            return (
              <a
                key={dest.id}
                href={dest.path}
                className={`nav-item ${isActive ? 'active' : ''}`}
                aria-current={isActive ? 'page' : undefined}
                onClick={(e) => {
                  e.preventDefault();
                  navigate(dest.path);
                }}
              >
                <svg className="nav-icon" viewBox="0 0 24 24" aria-hidden="true">
                  <path d={dest.icon} />
                </svg>
                <span>{dest.label}</span>
              </a>
            );
          })}
        </nav>

        <div className="sidebar-footer">
          Scoped Control Plane<br />
          <small>Password-Only Operator</small>
        </div>
      </aside>

      {/* Main Workspace */}
      <div className="app-workspace">
        <header className="app-header">
          <div className="header-left">
            <button
              type="button"
              className="mobile-menu-btn"
              aria-label="Toggle navigation"
              aria-expanded={mobileOpen}
              onClick={() => setMobileOpen(!mobileOpen)}
            >
              ☰
            </button>
            <div className="scope-chips">
              <span className="scope-account">{String(scopeData.account_id || 'account')}</span>
              <span className="badge badge-warn">{String(scopeData.environment || 'LIVE')}</span>
              <span className="badge badge-neutral">{String(scopeData.venue || 'bitget')}</span>
            </div>
          </div>

          <div className="header-tools">
            <span role="status" className="connection-indicator">
              <span className={`status-dot dot-${connection === 'live' ? 'good' : 'warn'}`} />
              {connection}
            </span>

            <select
              className="tool-select"
              aria-label="Display timezone"
              value={timezone}
              onChange={(e) => setTimezone(e.target.value)}
            >
              <option value="UTC">UTC</option>
              <option value="Local">Local</option>
            </select>

            <select
              className="tool-select"
              aria-label="Color theme"
              value={theme}
              onChange={(e) => setTheme(e.target.value)}
            >
              <option value="light">Light</option>
              <option value="dark">Dark</option>
              <option value="system">System</option>
            </select>

            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() => handleOpenAction('')}
              title="Manual command execution"
            >
              + Action
            </button>

            <details className="user-identity">
              <summary aria-haspopup="true">{principal.display_name}</summary>
              <div className="identity-dropdown">
                <div className="text-sm font-bold">{principal.display_name}</div>
                <div className="text-muted text-xs">Roles: {principal.roles.join(', ')}</div>
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  style={{marginTop: '4px'}}
                  onClick={async () => {
                    try {
                      await api('/auth/logout', {});
                      setPrincipal(null);
                      setCsrf('');
                      setEnvelope(null);
                      setSafety(null);
                      setCaps([]);
                      eventCursor.current = '';
                    } catch (err) {
                      setError(err);
                    }
                  }}
                >
                  Sign Out
                </button>
              </div>
            </details>
          </div>
        </header>

        {/* Attention Banner if Containment Latched */}
        <AttentionBanner latches={latches} />

        <main id="content" className="app-main" tabIndex={-1}>
          {/* Secondary Tabs */}
          {currentTabs.length > 0 && !currentRoute.resourceId && (
            <div className="tab-bar" role="tablist">
              {currentTabs.map((tab) => {
                const isActive =
                  location.pathname + location.search === tab.path ||
                  (tab.path === `/${currentRoute.screen}` &&
                    !location.search.includes('view='));
                return (
                  <button
                    key={tab.path}
                    type="button"
                    role="tab"
                    aria-selected={isActive}
                    className={`tab-btn ${isActive ? 'active' : ''}`}
                    onClick={() => navigate(tab.path)}
                  >
                    {tab.label}
                  </button>
                );
              })}
            </div>
          )}

          <ErrorBox error={error} />

          {/* Filters only on collection views where appropriate */}
          {showFilters && (
            <Filters
              key={currentRoute.readSection}
              section={currentRoute.readSection}
              value={filters}
              onApply={(val) => {
                setFilters(val);
                setCursor('');
                setEnvelope(null);
              }}
            />
          )}

          {/* Core Page Router */}
          {loading && !envelope ? (
            <StateView state="loading" />
          ) : (
            <>
              {currentRoute.destination === 'overview' && (
                <OverviewPage
                  envelope={envelope}
                  safety={safety}
                  readSection={currentRoute.readSection}
                  resourceId={currentRoute.resourceId}
                  onOpenAction={handleOpenAction}
                  render={(v) => <Value value={v} />}
                />
              )}
              {currentRoute.destination === 'trading' && (
                <TradingPage
                  envelope={envelope}
                  readSection={currentRoute.readSection}
                  resourceId={currentRoute.resourceId}
                  currentScreen={currentRoute.screen}
                  capabilities={caps}
                  onOpenAction={handleOpenAction}
                  render={(v) => <Value value={v} />}
                />
              )}
              {currentRoute.destination === 'signals' && (
                <SignalsPage
                  envelope={envelope}
                  readSection={currentRoute.readSection}
                  resourceId={currentRoute.resourceId}
                  onOpenAction={handleOpenAction}
                  render={(v) => <Value value={v} />}
                />
              )}
              {currentRoute.destination === 'activity' && (
                <ActivityPage
                  envelope={envelope}
                  readSection={currentRoute.readSection}
                  resourceId={currentRoute.resourceId}
                  onOpenAction={handleOpenAction}
                  reload={reload}
                  render={(v) => <Value value={v} />}
                />
              )}
              {currentRoute.destination === 'settings' && (
                <SettingsPage
                  envelope={envelope}
                  readSection={currentRoute.readSection}
                  resourceId={currentRoute.resourceId}
                  principal={principal}
                  onOpenAction={handleOpenAction}
                  reload={reload}
                  render={(v) => <Value value={v} />}
                />
              )}
            </>
          )}

          {/* Action Modal (Opens cleanly when action clicked, zero clutter when closed) */}
          {envelope && (
            <ActionPanel
              isOpen={actionModalOpen}
              onClose={() => setActionModalOpen(false)}
              principalId={principal.id}
              initialAction={actionModalAction}
              capabilities={caps}
              scope={scopeData}
              section={currentRoute.readSection}
              items={
                isObjectRecord(envelope.data) && Array.isArray(envelope.data.items)
                  ? envelope.data.items.filter(isObjectRecord)
                  : []
              }
              onChanged={() => {
                setActionModalOpen(false);
                reload();
              }}
            />
          )}
        </main>
      </div>
    </div>
  );
}

const rootEl = document.getElementById('root');
if (rootEl) {
  createRoot(rootEl).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>
  );
}
