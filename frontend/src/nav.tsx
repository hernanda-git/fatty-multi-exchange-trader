import React from 'react';
import type {PrimaryDestination, RouteInfo} from './types';

export const PRIMARY_DESTINATIONS: {id: PrimaryDestination; label: string; path: string; icon: string}[] = [
  {id: 'overview', label: 'Overview', path: '/overview', icon: 'M3 13h8V3H3v10zm0 8h8v-6H3v6zm10 0h8V11h-8v10zm0-18v6h8V3h-8z'},
  {id: 'trading', label: 'Trading', path: '/positions', icon: 'M3.5 18.49l6-6.01 4 4L22 6.92l-1.41-1.41-7.09 7.97-4-4L2 16.99z'},
  {id: 'signals', label: 'Signals', path: '/signals', icon: 'M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-6h2v6zm0-8h-2V7h2v2z'},
  {id: 'activity', label: 'Activity', path: '/audit', icon: 'M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm-2 10H7v-2h10v2zm0-4H7V7h10v2z'},
  {id: 'settings', label: 'Settings', path: '/config', icon: 'M19.14 12.94c.04-.3.06-.61.06-.94 0-.32-.02-.64-.07-.94l2.03-1.58c.18-.14.23-.41.12-.61l-1.92-3.32c-.12-.22-.37-.29-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54c-.04-.24-.24-.41-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96c-.22-.08-.47 0-.59.22L2.74 8.87c-.12.21-.08.47.12.61l2.03 1.58c-.05.3-.09.63-.09.94s.02.64.07.94l-2.03 1.58c-.18.14-.23.41-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61l-2.01-1.58zM12 15.6c-1.98 0-3.6-1.62-3.6-3.6s1.62-3.6 3.6-3.6 3.6 1.62 3.6 3.6-1.62 3.6-3.6 3.6z'},
];

const SCREEN_TO_DESTINATION: Record<string, PrimaryDestination> = {
  overview: 'overview',
  account: 'overview',
  positions: 'trading',
  orders: 'trading',
  protection: 'trading',
  trading: 'trading',
  signals: 'signals',
  dispatches: 'signals',
  audit: 'activity',
  jobs: 'activity',
  approvals: 'activity',
  management: 'activity',
  notifications: 'activity',
  config: 'settings',
  recovery: 'settings',
  services: 'settings',
  releases: 'settings',
  backups: 'settings',
  access: 'settings',
};

export function parseRoute(pathname: string, search: string): RouteInfo {
  const parts = pathname.split('/').filter(Boolean);
  const rawFirst = parts[0] || 'overview';
  const query = new URLSearchParams(search);
  const viewParam = query.get('view') || '';

  // Direct mapping of first path segment
  let screen = rawFirst;
  let subSection = parts[1] ? decodeURIComponent(parts[1]) : '';
  let resourceId = '';

  // Normalize legacy shorthand roots: e.g. /releases/123, /jobs/123, /approvals/123, /fills/123
  if (rawFirst === 'releases') {
    screen = 'services';
    subSection = 'releases';
    resourceId = parts.slice(1).map(decodeURIComponent).join('/');
  } else if (rawFirst === 'jobs') {
    screen = 'audit';
    subSection = 'jobs';
    resourceId = parts.slice(1).map(decodeURIComponent).join('/');
  } else if (rawFirst === 'approvals') {
    screen = 'audit';
    subSection = 'approvals';
    resourceId = parts.slice(1).map(decodeURIComponent).join('/');
  } else if (rawFirst === 'fills') {
    screen = 'orders';
    subSection = 'fills';
    resourceId = parts.slice(1).map(decodeURIComponent).join('/');
  } else if (rawFirst === 'dispatches') {
    screen = 'signals';
    subSection = 'dispatches';
    resourceId = parts.slice(1).map(decodeURIComponent).join('/');
  } else if (['signals', 'orders', 'audit', 'config', 'recovery', 'services', 'access', 'account'].includes(rawFirst)) {
    // Check if parts[1] is a known sub-collection vs a resource ID
    const knownSubs: Record<string, string[]> = {
      signals: ['dispatches'],
      orders: ['fills'],
      audit: ['jobs', 'approvals', 'audit'],
      config: ['history', 'drafts'],
      recovery: ['baselines'],
      services: ['releases'],
      access: ['principals', 'sessions', 'policy'],
      account: ['pnl', 'reservations', 'admission-snapshots'],
    };
    if (parts[1] && knownSubs[rawFirst]?.includes(parts[1])) {
      subSection = parts[1];
      resourceId = parts.slice(2).map(decodeURIComponent).join('/');
    } else {
      subSection = viewParam ? viewParam.split('/')[1] || viewParam : '';
      resourceId = parts.slice(1).map(decodeURIComponent).join('/');
    }
  } else {
    resourceId = parts.slice(1).map(decodeURIComponent).join('/');
  }

  const destination = SCREEN_TO_DESTINATION[screen] || 'overview';

  // Determine readSection for API fetch
  let readSection = screen;
  if (screen === 'trading') {
    readSection = 'positions';
  } else if (subSection) {
    if (screen === 'signals' && subSection === 'dispatches') readSection = 'dispatches';
    else if (screen === 'orders' && subSection === 'fills') readSection = 'fills';
    else if (screen === 'audit' && subSection === 'jobs') readSection = 'jobs';
    else if (screen === 'audit' && subSection === 'approvals') readSection = 'approvals';
    else if (screen === 'services' && subSection === 'releases') readSection = 'releases';
    else if (['history', 'drafts'].includes(subSection) && screen === 'config') readSection = `config/${subSection}`;
    else if (subSection === 'baselines' && screen === 'recovery') readSection = 'recovery/baselines';
    else if (['principals', 'sessions', 'policy'].includes(subSection) && screen === 'access') readSection = `access/${subSection}`;
    else if (['pnl', 'reservations', 'admission-snapshots'].includes(subSection) && screen === 'account') readSection = `account/${subSection}`;
  } else if (viewParam) {
    readSection = viewParam;
  }

  return {
    destination,
    screen,
    subSection,
    id: parts.slice(1).map(decodeURIComponent).join('/'),
    readSection,
    resourceId,
    rawPath: pathname,
  };
}

export function navigate(path: string) {
  if (path === location.pathname + location.search) return;
  history.pushState(null, '', path);
  window.dispatchEvent(new PopStateEvent('popstate'));
}

export function Link({
  to,
  children,
  className,
  title,
  'aria-label': ariaLabel,
  'aria-current': ariaCurrent,
}: {
  to: string;
  children: React.ReactNode;
  className?: string;
  title?: string;
  'aria-label'?: string;
  'aria-current'?: 'page' | 'step' | 'location' | 'date' | 'time' | boolean;
}) {
  const targetRoot = to.split('/')[1] || '';
  const currentRoot = location.pathname.split('/')[1] || '';
  // Preserve query for same-section links unless new destination has explicit query
  const resolved = targetRoot === currentRoot && !to.includes('?') && location.search ? to + location.search : to;

  return (
    <a
      href={resolved}
      className={className}
      title={title}
      aria-label={ariaLabel}
      aria-current={ariaCurrent}
      onClick={(e) => {
        if (e.button === 0 && !e.ctrlKey && !e.metaKey && !e.shiftKey && !e.altKey) {
          e.preventDefault();
          navigate(resolved);
        }
      }}
    >
      {children}
    </a>
  );
}
