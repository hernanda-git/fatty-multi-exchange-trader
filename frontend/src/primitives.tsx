import React from 'react';
import {Link} from './nav';
import {type RecordData, type ApiError, isObjectRecord} from './types';
export const human = (s: string) =>
  s.replace(/[._]/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());

export function formatDateTime(iso: string, timezone = 'UTC'): string {
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: 'medium',
      timeStyle: 'medium',
      timeZone: timezone === 'Local' ? undefined : timezone,
    }).format(d);
  } catch {
    return iso;
  }
}

export function formatNumber(val: string | number): string {
  const str = String(val);
  if (!/^-?\d+(?:\.\d+)?$/.test(str)) return str;
  const [whole, fraction] = str.split('.');
  const formattedWhole = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return fraction !== undefined ? `${formattedWhole}.${fraction}` : formattedWhole;
}

export function Value({
  value,
  depth = 0,
  compact = false,
}: {
  value: unknown;
  depth?: number;
  compact?: boolean;
}): React.ReactNode {
  if (value === null || value === undefined) {
    return <span className="muted">Not observed</span>;
  }
  if (typeof value === 'boolean') {
    return (
      <span className={`badge ${value ? 'badge-positive' : 'badge-neutral'}`}>
        {value ? 'Yes' : 'No'}
      </span>
    );
  }
  if (typeof value === 'number') {
    return <span className="tabular-num">{formatNumber(value)}</span>;
  }
  if (typeof value === 'string') {
    if (value.startsWith('/') && !value.startsWith('//')) {
      return <Link to={value}>{value}</Link>;
    }
    if (/^\d{4}-\d\d-\d\dT/.test(value) && Number.isFinite(Date.parse(value))) {
      const tz = document.documentElement.dataset.timezone || 'UTC';
      return (
        <time dateTime={value} title={`Original UTC: ${value}`} className="tabular-num">
          {formatDateTime(value, tz)}
        </time>
      );
    }
    if (/^-?\d+(?:\.\d+)?$/.test(value)) {
      return <span className="tabular-num" title={value}>{formatNumber(value)}</span>;
    }
    if (value.length > 200 && !compact) {
      return <span className="preserve text-wrap">{value}</span>;
    }
    return <span>{value}</span>;
  }
  if (Array.isArray(value)) {
    if (!value.length) return <span className="muted">Empty</span>;
    return (
      <div className="array-list">
        {value.map((v, i) => (
          <div className="array-item" key={i}>
            <Value value={v} depth={depth + 1} compact={compact} />
          </div>
        ))}
      </div>
    );
  }
  if (isObjectRecord(value)) {
    if (typeof value.section === 'string' && value.id !== undefined) {
      const section = value.section;
      const path =
        section === 'jobs'
          ? 'audit/jobs'
          : section === 'dispatches'
          ? 'signals/dispatches'
          : section === 'fills'
          ? 'orders/fills'
          : section;
      return (
        <Link to={`/${path}/${encodeURIComponent(String(value.id))}`}>
          {human(section)} · {String(value.id).slice(0, 16)}
        </Link>
      );
    }
    return (
      <dl className={depth === 0 ? 'record-grid' : 'record-nested'}>
        {Object.entries(value).map(([k, v]) => (
          <div key={k} className="record-field">
            <dt>{human(k)}</dt>
            <dd>
              <Value value={v} depth={depth + 1} compact={compact} />
            </dd>
          </div>
        ))}
      </dl>
    );
  }
  return String(value);
}

export function StatusBadge({
  status,
  label,
  title,
}: {
  status: 'fresh' | 'stale' | 'good' | 'warn' | 'danger' | 'unknown' | 'neutral';
  label?: string;
  title?: string;
}) {
  const displayLabel = label || human(status);
  return (
    <span className={`status-badge status-${status}`} title={title}>
      <span className={`status-dot dot-${status}`} aria-hidden="true" />
      {displayLabel}
    </span>
  );
}

export function AttentionBanner({latches}: {latches: unknown}) {
  if (!Array.isArray(latches)) {
    return (
      <div className="attention-bar banner-warn" role="status">
        <span className="banner-icon">⚠️</span>
        <div>
          <strong>Safety latch observation unverified</strong> — No safe or unlatched state inferred.
        </div>
        <Link to="/recovery" className="banner-action">Inspect Recovery</Link>
      </div>
    );
  }

  const active = latches.filter(
    (row): row is RecordData => isObjectRecord(row) && row.active === true
  );

  if (!active.length) {
    return null;
  }

  return (
    <div className="attention-bar banner-danger" role="status">
      <span className="banner-icon">🛡️</span>
      <div className="banner-content">
        <strong>Trading Containment Active:</strong> {active.map((row, i) => (
          <span key={String(row.scope || i)} className="latch-item">
            <strong>{String(row.scope || 'Scope')}</strong>: {String(row.reason || 'Reason unobserved')}
            {row.latched_at ? ` (${formatDateTime(String(row.latched_at))})` : ''}
          </span>
        ))}
      </div>
      <Link to="/recovery" className="banner-action">Safety & Recovery →</Link>
    </div>
  );
}

export function ErrorBox({error}: {error: unknown}) {
  if (!error) return null;
  let reqId: string | null = null;
  let code: string | null = null;
  if (isObjectRecord(error) && 'detail' in error && isObjectRecord(error.detail)) {
    const detail = error.detail;
    if ('request_id' in detail && typeof detail.request_id === 'string') {
      reqId = detail.request_id;
    }
    if ('code' in detail && typeof detail.code === 'string') {
      code = detail.code;
    }
  }
  const message = error instanceof Error ? error.message : String(error);

  return (
    <div role="alert" className="error-box">
      <div className="error-title">
        <span className="error-icon" aria-hidden="true">✕</span>
        <span>{code ? `${human(code)}: ` : ''}{message}</span>
      </div>
      {reqId && <div className="error-meta">Request ID: <code>{reqId}</code></div>}
    </div>
  );
}

export function StateView({
  state,
  message,
  onClearFilters,
}: {
  state: 'loading' | 'empty' | 'filtered_empty' | 'stale' | 'error' | 'unavailable';
  message?: string;
  onClearFilters?: () => void;
}) {
  if (state === 'loading') {
    return (
      <div className="state-panel state-loading" role="status">
        <div className="spinner" aria-hidden="true" />
        <p>{message || 'Loading verified records and evidence…'}</p>
      </div>
    );
  }

  if (state === 'filtered_empty') {
    return (
      <div className="state-panel state-empty">
        <div className="state-icon">🔍</div>
        <h3>No matching records</h3>
        <p>{message || 'No records match the current filter criteria.'}</p>
        {onClearFilters && (
          <button type="button" className="btn btn-secondary" onClick={onClearFilters}>
            Clear filters
          </button>
        )}
      </div>
    );
  }

  if (state === 'empty') {
    return (
      <div className="state-panel state-empty">
        <div className="state-icon">📋</div>
        <h3>No records returned</h3>
        <p>{message || 'Successful scoped observation returned no records.'}</p>
      </div>
    );
  }

  if (state === 'unavailable') {
    return (
      <div className="state-panel state-unavailable">
        <div className="state-icon">🔒</div>
        <h3>Observation unavailable</h3>
        <p>{message || 'The requested observation or service is currently unavailable.'}</p>
      </div>
    );
  }

  return null;
}

export function EvidenceDisclosure({evidence}: {evidence: unknown}) {
  const items = Array.isArray(evidence) ? evidence : isObjectRecord(evidence) ? [evidence] : [];
  if (!items.length) return null;

  return (
    <details className="evidence-disclosure">
      <summary className="evidence-summary">
        <span className="evidence-label">Authoritative Evidence & Proofs</span>
        <span className="evidence-count">({items.length} records)</span>
      </summary>
      <div className="evidence-body">
        {items.map((item, i) => {
          const rec = isObjectRecord(item) ? item : {};
          const time = rec.observed_at ? Date.parse(String(rec.observed_at)) : NaN;
          const age = Number.isFinite(time) ? Math.max(0, Math.floor((Date.now() - time) / 1000)) : null;
          const stale = Boolean(rec.freshness === 'stale' || (age !== null && typeof rec.stale_after_seconds === 'number' && age > rec.stale_after_seconds));
          return (
            <div key={i} className="evidence-item">
              <div className="evidence-meta">
                <StatusBadge status={stale ? 'warn' : rec.freshness === 'fresh' ? 'fresh' : 'unknown'} label={stale ? 'Stale' : String(rec.freshness || 'Verified')} />
                <strong>{String(rec.id || rec.source || 'Evidence')}</strong>
                <span className="muted">{age === null ? 'time unknown' : `${age}s ago`}</span>
                {Boolean(rec.read_status) && <span className="badge badge-neutral">{String(rec.read_status)}</span>}
              </div>
              <Value value={rec} compact />
            </div>
          );
        })}
      </div>
    </details>
  );
}
