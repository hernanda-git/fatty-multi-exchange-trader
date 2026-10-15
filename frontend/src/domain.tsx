import React, {type ReactNode} from 'react';
import {type RecordData, isObjectRecord} from './types';
import {Link} from './nav';
import {Value, formatDateTime, formatNumber, human} from './primitives';

type Column = {label: string; paths: string[]};

export const columns: Record<string, Column[]> = {
  signals: [
    {label: 'Source / channel', paths: ['source', 'channel_id']},
    {label: 'Message / revision', paths: ['message_id', 'revision_hash']},
    {label: 'Received / intake', paths: ['received_at', 'intake_state']},
    {label: 'Classification', paths: ['classification', 'symbol', 'direction']},
  ],
  dispatches: [
    {label: 'Symbol / direction', paths: ['symbol', 'direction']},
    {label: 'Dispatch outcome', paths: ['state', 'terminal_reason']},
    {label: 'Source', paths: ['source_id', 'revision']},
    {label: 'Route / admission', paths: ['route', 'admission_state']},
  ],
  positions: [
    {label: 'Contract / ownership', paths: ['symbol', 'direction', 'ownership']},
    {label: 'Provider / owned quantity', paths: ['provider.total', 'owned_residual']},
    {label: 'Entry / mark / liquidation', paths: ['provider.openPriceAvg', 'provider.markPrice', 'provider.liquidationPrice']},
    {label: 'Unrealized PnL', paths: ['provider.unrealizedPL']},
    {label: 'Native proof / fallback', paths: ['native_protection_proof', 'fallback_enforcement']},
  ],
  orders: [
    {label: 'Contract / ownership', paths: ['symbol', 'direction', 'ownership']},
    {label: 'Intent / provider status', paths: ['state', 'provider_terminal']},
    {label: 'Requested / filled / residual', paths: ['quantity', 'filled_quantity', 'remaining_quantity']},
    {label: 'Price / role', paths: ['price', 'order_type', 'role']},
    {label: 'Provider / client identity', paths: ['provider_order_id', 'client_order_id']},
  ],
  fills: [
    {label: 'Contract / side', paths: ['symbol', 'direction']},
    {label: 'Quantity / price', paths: ['quantity', 'price']},
    {label: 'Realized / fee', paths: ['realized_pnl', 'fee', 'fee_ccy']},
    {label: 'Execution evidence', paths: ['filled_at', 'provider_fill_id', 'client_order_id']},
  ],
  protection: [
    {label: 'Contract / owned residual', paths: ['symbol', 'direction', 'owned_residual']},
    {label: 'Native enforcement proof', paths: ['native_protection_proof', 'native_protection_reason']},
    {label: 'Missing SL / TP', paths: ['missing_sl', 'missing_tp']},
    {label: 'Fallback enforcement', paths: ['fallback_enforcement']},
    {label: 'Epoch / capability', paths: ['provider_position_epoch', 'capability']},
  ],
  management: [
    {label: 'Instruction', paths: ['action', 'symbol', 'raw_text']},
    {label: 'Source / revision', paths: ['source_message_id', 'revision']},
    {label: 'Mutation / outcome', paths: ['state', 'terminal_reason']},
    {label: 'Claim / time', paths: ['claimed_by', 'created_at', 'updated_at']},
  ],
  services: [
    {label: 'Service', paths: ['service_id', 'name']},
    {label: 'Observed / health', paths: ['state', 'health', 'running']},
    {label: 'Release / image', paths: ['release_sha', 'image_digest']},
    {label: 'Attestation', paths: ['observed_at', 'last_progress_at']},
  ],
  notifications: [
    {label: 'Relay state', paths: ['state', 'attempts']},
    {label: 'Event / deduplication', paths: ['event_kind', 'dedup_key']},
    {label: 'Acceptance / acknowledgement', paths: ['sent_at', 'failed_at']},
    {label: 'Retry / lease', paths: ['next_attempt_at', 'lease_until']},
  ],
  jobs: [
    {label: 'Command / risk', paths: ['action', 'danger_tier']},
    {label: 'State / phase', paths: ['state', 'phase', 'reconciliation_required']},
    {label: 'Actor / time', paths: ['actor_id', 'created_at']},
    {label: 'Claim / result', paths: ['effect_claimed', 'result.state']},
  ],
  approvals: [
    {label: 'Job / actor', paths: ['job_id', 'actor_id', 'approver_id']},
    {label: 'Decision', paths: ['state', 'decision']},
    {label: 'Bound digest / time', paths: ['preview_digest', 'created_at']},
  ],
  audit: [
    {label: 'Action / outcome', paths: ['action', 'outcome']},
    {label: 'Actor / channel', paths: ['actor_id', 'channel']},
    {label: 'Resource / correlation', paths: ['resource_id', 'job_id', 'request_id']},
    {label: 'Time', paths: ['created_at']},
  ],
  backups: [
    {label: 'Created / size', paths: ['created_at', 'size_bytes']},
    {label: 'Integrity / encryption', paths: ['sha256', 'encryption', 'key_reference']},
    {label: 'Restore evidence', paths: ['state', 'verified_at', 'restore_drill_at']},
    {label: 'Release / schema', paths: ['release_id', 'schema_version']},
  ],
  releases: [
    {label: 'Release', paths: ['release_id', 'sha', 'version']},
    {label: 'Approval / state', paths: ['approved', 'state']},
    {label: 'Created', paths: ['created_at']},
  ],
  'config/history': [
    {label: 'Requested revision', paths: ['version', 'base_version']},
    {label: 'Lifecycle', paths: ['state', 'kind']},
    {label: 'Actor / time', paths: ['actor_id', 'created_at']},
  ],
  'config/drafts': [
    {label: 'Draft revision', paths: ['version', 'base_version']},
    {label: 'Status', paths: ['state']},
    {label: 'Creator', paths: ['actor_id', 'created_at']},
  ],
  'access/principals': [
    {label: 'Identity', paths: ['display_name']},
    {label: 'Roles / scopes', paths: ['roles', 'scopes']},
    {label: 'Enabled', paths: ['enabled']},
    {label: 'Created', paths: ['created_at']},
  ],
  'access/sessions': [
    {label: 'Principal', paths: ['principal_id']},
    {label: 'Authenticated', paths: ['authenticated_at']},
    {label: 'Last seen / expires', paths: ['last_seen_at', 'absolute_expires_at']},
    {label: 'Revoked', paths: ['revoked_at']},
  ],
  'account/reservations': [
    {label: 'Contract / binding', paths: ['symbol', 'binding_symbol', 'client_order_id']},
    {label: 'Reserved margin', paths: ['amount', 'margin_usdt']},
    {label: 'Lifecycle', paths: ['state', 'created_at']},
  ],
  'account/admission-snapshots': [
    {label: 'Account / currency', paths: ['account_id', 'margin_coin']},
    {label: 'Equity / available', paths: ['equity', 'available_balance']},
    {label: 'Admission capture', paths: ['captured_at']},
  ],
  'recovery/baselines': [
    {label: 'Account / historical cutoff', paths: ['account_id', 'historical_before']},
    {label: 'Approval / policy', paths: ['approval_reference', 'uncertainty_policy']},
    {label: 'Created', paths: ['created_at']},
  ],
};

export function observed(record: RecordData, path: string): unknown {
  let value: unknown = record;
  for (const part of path.split('.')) {
    if (!isObjectRecord(value)) return null;
    value = value[part];
  }
  return value ?? null;
}

export function DomainTable({
  section,
  items,
  render,
}: {
  section: string;
  items: RecordData[];
  render: (value: unknown) => ReactNode;
}) {
  const fields = columns[section] || [{label: 'Record status', paths: ['state', 'kind', 'created_at']}];

  return (
    <div className="table-responsive-wrapper">
      <div className="table-wrap desktop-records">
        <table className="data-table">
          <thead>
            <tr>
              <th scope="col">Exact record</th>
              {fields.map((c) => (
                <th scope="col" key={c.label}>
                  {c.label}
                </th>
              ))}
              <th scope="col">Full evidence</th>
            </tr>
          </thead>
          <tbody>
            {items.map((row, i) => (
              <tr key={String(row.id ?? i)}>
                <th scope="row" className="record-header-cell">
                  {render(row.id === undefined ? null : {section, id: row.id})}
                  <small className="cell-subtext">{String(row.symbol || row.display_name || row.action || '')}</small>
                </th>
                {fields.map((c) => (
                  <td key={c.label}>
                    <dl className="record-compact">
                      {c.paths.map((path) => (
                        <div key={path}>
                          <dt>{path.split('.').pop()?.replace(/_/g, ' ')}</dt>
                          <dd>{render(observed(row, path))}</dd>
                        </div>
                      ))}
                    </dl>
                  </td>
                ))}
                <td>
                  <details className="evidence-inline-disclosure">
                    <summary className="evidence-link">Inspect fields</summary>
                    <div className="evidence-popup">
                      <Value value={row} compact />
                    </div>
                  </details>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="mobile-records">
        {items.map((row, i) => (
          <article className="card resource-card" key={String(row.id ?? i)}>
            <div className="resource-card-header">
              <h3>{render(row.id === undefined ? null : {section, id: row.id})}</h3>
              {Boolean(row.symbol || row.display_name || row.action) && (
                <span className="badge badge-neutral">{String(row.symbol || row.display_name || row.action)}</span>
              )}
            </div>
            <div className="resource-card-body">
              {fields.map((c) => (
                <div key={c.label} className="field-group">
                  <h4>{c.label}</h4>
                  <dl className="record-compact">
                    {c.paths.map((path) => (
                      <div key={path}>
                        <dt>{path.split('.').pop()?.replace(/_/g, ' ')}</dt>
                        <dd>{render(observed(row, path))}</dd>
                      </div>
                    ))}
                  </dl>
                </div>
              ))}
            </div>
            <details className="resource-card-footer">
              <summary>Full record payload</summary>
              <Value value={row} compact />
            </details>
          </article>
        ))}
      </div>
    </div>
  );
}

const panels: Record<string, [string, string, string][]> = {
  overview: [
    ['provider', 'Provider account & open exposure', '/account'],
    ['ledger', 'Ledger-only intents, positions & fills', '/orders'],
    ['latches', 'Independent safety latches', '/recovery'],
    ['services', 'Worker and service observations', '/services'],
    ['effective_config', 'Effective mode & independent gates', '/config'],
    ['clock', 'Exchange clock evidence', '/recovery'],
    ['stream', 'Protection stream evidence', '/protection'],
    ['admission_snapshots', 'Immutable admission captures', '/account'],
  ],
  account: [
    ['provider', 'Authenticated provider balances', '/account'],
    ['provider_identity', 'Provider account identity proof', '/account'],
    ['provider_positions', 'Provider exposure and uPnL', '/positions'],
    ['ledger_pnl', 'Ledger realized PnL & fees (not account-wide net)', '/account/pnl'],
    ['admission_snapshots', 'Admission-time balances', '/account/admission-snapshots'],
    ['reservations', 'Durable margin reservations', '/account/reservations'],
    ['baseline_exclusions', 'Excluded baseline history', '/recovery/baselines'],
  ],
  recovery: [
    ['latches', 'Latched scopes, reasons & times', '/recovery'],
    ['unresolved_intents', 'Ambiguous claims needing GET readback', '/orders'],
    ['reservations', 'Reservations remain independently accounted', '/account/reservations'],
    ['baselines', 'Audited historical exclusions', '/recovery/baselines'],
    ['provider', 'Fresh provider reconciliation evidence', '/positions'],
    ['reconciliation', 'Persisted reconciliation state', '/recovery'],
    ['proof_status', 'Approved recovery proof', '/recovery'],
  ],
  config: [
    ['requested', 'Requested revisions (not effective values)', '/config/history'],
    ['effective', 'Observed effective settings & gates', '/config'],
    ['host', 'Host-side configuration evidence', '/services'],
  ],
  access: [
    ['principals', 'Scoped identities, roles & enrollment', '/access/principals'],
    ['sessions', 'Sessions and revocation evidence', '/access/sessions'],
    ['policy', 'Policy and user-verification requirements', '/access/policy'],
  ],
};

export function DomainDetail({
  section,
  data,
  render,
}: {
  section: string;
  data: unknown;
  render: (value: unknown) => ReactNode;
}) {
  if (!data || typeof data !== 'object' || Array.isArray(data)) {
    return <section className="card">{render(data)}</section>;
  }

  const record = data as RecordData;
  const cards = panels[section];

  if (Object.hasOwn(record, 'items') && record.items === null) {
    return (
      <section className="card state-panel state-unavailable">
        <h2>Observation unavailable</h2>
        <p>
          The provider or persisted projection did not return a complete usable collection.
          This is not a confirmed empty account. Inspect the evidence errors above before considering any command.
        </p>
        {render(record)}
      </section>
    );
  }

  if (cards) {
    return (
      <div className="domain-cards-grid">
        {cards.map(([key, title, path]) => (
          <section className="card" key={key}>
            <div className="card-header">
              <h2>{title}</h2>
              <Link to={path} className="link-subtle">
                {path} →
              </Link>
            </div>
            {render(record[key] ?? null)}
          </section>
        ))}
        <details className="card evidence-disclosure-card">
          <summary>Full snapshot payload & coverage evidence</summary>
          <Value value={record} />
        </details>
      </div>
    );
  }

  const linked =
    record.linked && typeof record.linked === 'object' && !Array.isArray(record.linked)
      ? (record.linked as RecordData)
      : {};

  const timeline =
    section === 'signals'
      ? ['revisions', 'analysis_runs', 'canonical_signals', 'dispatches', 'management']
      : section === 'dispatches'
      ? ['canonical_signal', 'manual_entry', 'transitions', 'reservations', 'intents']
      : section === 'orders' || section === 'fills'
      ? ['intent', 'entry_reservation', 'provider_orders', 'provider_plan_orders', 'provider_fills', 'fills', 'reconciliation', 'close_bindings', 'baseline']
      : section === 'management'
      ? ['cancelled_entries', 'provider_intents']
      : [];

  return (
    <div className="domain-detail-stack">
      {columns[section] && (
        <section className="card">
          <div className="card-header">
            <h2>Observed Domain Record</h2>
          </div>
          <DomainTable section={section} items={[record]} render={render} />
        </section>
      )}

      {section === 'signals' && (
        <section className="card original-message-card">
          <div className="card-header">
            <div>
              <span className="eyebrow">IMMUTABLE SOURCE</span>
              <h2>Message Text & Metadata</h2>
            </div>
          </div>
          <div className="message-content">
            <blockquote className="raw-message-body">{String(record.raw_text ?? 'No text observed')}</blockquote>
            <div className="message-meta-row">
              <span>Channel: <strong>{String(record.channel_id ?? 'unknown')}</strong></span>
              <span>Message ID: <strong>{String(record.message_id ?? 'unknown')}</strong></span>
              <span>Revision: <code>{String(record.revision_hash ?? 'unknown').slice(0, 16)}</code></span>
            </div>
          </div>
        </section>
      )}

      {section === 'signals' && Array.isArray(linked.revisions) && (
        <section className="card">
          <div className="card-header">
            <h2>Immutable Message Revisions</h2>
          </div>
          <div className="revisions-list">
            {linked.revisions.map((rev, i) => {
              const r = isObjectRecord(rev) ? rev : {};
              return (
                <div key={String(r.id ?? i)} className="revision-item">
                  {render({section: 'signals', id: r.id})}
                  <span>Hash: <code>{String(r.revision_hash ?? 'none').slice(0, 16)}</code></span>
                  <span>Received: {r.received_at ? formatDateTime(String(r.received_at)) : 'unknown'}</span>
                </div>
              );
            })}
          </div>
        </section>
      )}

      {section === 'signals' && Array.isArray(record.media) && record.media.length > 0 && (
        <section className="card media-card">
          <div className="card-header">
            <h2>Authenticated Source Media</h2>
          </div>
          <div className="media-grid">
            {record.media.map((med, i) => {
              const m = isObjectRecord(med) ? med : {};
              const url = `/api/v1/signals/${encodeURIComponent(String(record.id))}/media/${encodeURIComponent(String(m.id))}`;
              return (
                <figure key={String(m.id ?? i)} className="media-item">
                  <a href={url} target="_blank" rel="noopener noreferrer">
                    <img className="source-media-img" src={url} alt="Authenticated signal media" loading="lazy" />
                  </a>
                  <figcaption>
                    {String(m.mime_type ?? 'MIME unknown')} · {formatNumber(Number(m.size_bytes ?? 0))} bytes
                  </figcaption>
                </figure>
              );
            })}
          </div>
        </section>
      )}

      {timeline.length > 0 && (
        <section className="card timeline-card">
          <div className="card-header">
            <h2>Evidence-Linked Trace Timeline</h2>
          </div>
          <ol className="trace-timeline">
            {timeline.map((key) => (
              <li key={key} className="timeline-node">
                <h3>{human(key)}</h3>
                <div className="timeline-node-content">{render(linked[key] ?? null)}</div>
              </li>
            ))}
          </ol>
          <p className="muted">Missing steps remain explicitly unobserved. Timeline position does not assert an effect occurred.</p>
        </section>
      )}

      <details className="card evidence-disclosure-card" open={!columns[section]}>
        <summary>Exact Record Details & Linked Versions</summary>
        <Value value={record} />
      </details>
    </div>
  );
}

export function LatchBanner({value}: {value: unknown}) {
  if (!Array.isArray(value)) {
    return (
      <div className="alert alert-warning" role="status">
        Safety latch observation unavailable. No safe or unlatched state is inferred.
      </div>
    );
  }

  const active = value.filter(
    (row): row is RecordData => isObjectRecord(row) && row.active === true
  );

  if (!active.length) {
    return null;
  }

  return (
    <div className="alert alert-danger" role="status">
      <strong>Containment Active — Admission / Mutation Policy-Gated</strong>
      {active.map((row, i) => (
        <p key={String(row.scope ?? i)}>
          <strong>{String(row.scope ?? 'Unknown scope')}</strong> · {String(row.reason ?? 'Reason unobserved')}
          <small>
            Latched {row.latched_at ? formatDateTime(String(row.latched_at)) : 'unknown'} ·
            Updated {row.updated_at ? formatDateTime(String(row.updated_at)) : 'unknown'}
          </small>
        </p>
      ))}
      <Link to="/recovery" className="link-action">Inspect exact recovery proof and containment evidence →</Link>
    </div>
  );
}
