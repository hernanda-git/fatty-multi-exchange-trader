import React, {type ReactNode} from 'react';
import {type RecordData, isObjectRecord} from './types';
import {Link} from './nav';
import {Value, StatusBadge, formatDateTime, formatNumber, human} from './primitives';

export function observed(record: RecordData, path: string): unknown {
  let value: unknown = record;
  for (const part of path.split('.')) {
    if (!isObjectRecord(value)) return null;
    value = value[part];
  }
  return value ?? null;
}

// --------------------------------------------------------------------------
// Dedicated High-Clarity Table Renderers
// --------------------------------------------------------------------------

export function PositionsTable({
  items,
  onClose,
}: {
  items: RecordData[];
  onClose?: (item: RecordData) => void;
}) {
  if (!items.length) {
    return (
      <div className="empty-state-box">
        <p className="empty-title">No Open Positions</p>
        <p className="empty-subtext">The bot and account currently have zero market exposure.</p>
        <div style={{marginTop: '10px'}}>
          <Link to="/trading" className="btn btn-primary btn-sm">
            Open New Trade →
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="clean-table-wrapper">
      <table className="clean-table">
        <thead>
          <tr>
            <th scope="col">Contract</th>
            <th scope="col">Side</th>
            <th scope="col" className="text-right">Size</th>
            <th scope="col" className="text-right">Entry Price</th>
            <th scope="col" className="text-right">Mark Price</th>
            <th scope="col" className="text-right">Liq. Price</th>
            <th scope="col" className="text-right">Unrealized P&amp;L</th>
            <th scope="col">Protection</th>
            <th scope="col" className="text-right">Action</th>
          </tr>
        </thead>
        <tbody>
          {items.map((row, i) => {
            const sym = String(row.symbol || 'UNKNOWN');
            const dir = String(row.direction || 'LONG').toUpperCase();
            const provider = isObjectRecord(row.provider) ? row.provider : {};
            const total = String(provider.total || row.owned_residual || '0');
            const entry = String(provider.openPriceAvg || '0');
            const mark = String(provider.markPrice || '0');
            const liq = String(provider.liquidationPrice || '0');
            const upnl = Number(provider.unrealizedPL || 0);
            const prot = String(row.native_protection_proof || 'UNKNOWN');

            return (
              <tr key={String(row.id ?? i)}>
                <td className="font-bold">
                  <Link to={`/positions/${encodeURIComponent(String(row.id || sym))}`}>
                    {sym}
                  </Link>
                </td>
                <td>
                  <span className={`badge ${dir === 'LONG' ? 'badge-positive' : 'badge-danger'}`}>
                    {dir}
                  </span>
                </td>
                <td className="text-right tabular-num font-mono">{formatNumber(total)}</td>
                <td className="text-right tabular-num font-mono">${formatNumber(entry)}</td>
                <td className="text-right tabular-num font-mono">${formatNumber(mark)}</td>
                <td className="text-right tabular-num font-mono text-danger">${formatNumber(liq)}</td>
                <td className={`text-right tabular-num font-mono font-bold ${upnl >= 0 ? 'text-success' : 'text-danger'}`}>
                  {upnl >= 0 ? '+' : ''}${formatNumber(upnl.toFixed(2))}
                </td>
                <td>
                  <StatusBadge
                    status={prot === 'PROVED_ACTIVE' || prot === 'ACTIVE' ? 'fresh' : 'warn'}
                    label={prot === 'PROVED_ACTIVE' || prot === 'ACTIVE' ? 'Protected' : prot}
                  />
                </td>
                <td className="text-right">
                  {onClose && (
                    <button
                      type="button"
                      className="btn btn-secondary btn-sm"
                      onClick={() => onClose(row)}
                    >
                      Close
                    </button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function OrdersTable({
  items,
  onCancel,
}: {
  items: RecordData[];
  onCancel?: (item: RecordData) => void;
}) {
  if (!items.length) {
    return (
      <div className="empty-state-box">
        <p className="empty-title">No Working Orders</p>
        <p className="empty-subtext">There are no pending limit or trigger orders in the orderbook.</p>
      </div>
    );
  }

  return (
    <div className="clean-table-wrapper">
      <table className="clean-table">
        <thead>
          <tr>
            <th scope="col">Contract</th>
            <th scope="col">Side</th>
            <th scope="col">Type / Role</th>
            <th scope="col" className="text-right">Order Price</th>
            <th scope="col" className="text-right">Quantity</th>
            <th scope="col" className="text-right">Remaining</th>
            <th scope="col">State</th>
            <th scope="col" className="text-right">Action</th>
          </tr>
        </thead>
        <tbody>
          {items.map((row, i) => {
            const sym = String(row.symbol || 'UNKNOWN');
            const dir = String(row.direction || 'LONG').toUpperCase();
            const orderType = String(row.order_type || 'LIMIT');
            const role = String(row.role || 'ENTRY');
            const price = String(row.price || '0');
            const qty = String(row.quantity || '0');
            const remaining = String(row.remaining_quantity || qty);
            const state = String(row.state || 'WORKING');

            return (
              <tr key={String(row.id ?? i)}>
                <td className="font-bold">
                  <Link to={`/orders/${encodeURIComponent(String(row.id || ''))}`}>
                    {sym}
                  </Link>
                  <div className="font-mono text-muted text-xs">{String(row.client_order_id || '')}</div>
                </td>
                <td>
                  <span className={`badge ${dir === 'LONG' ? 'badge-positive' : 'badge-danger'}`}>
                    {dir}
                  </span>
                </td>
                <td>
                  <span className="badge badge-neutral">{orderType}</span>{' '}
                  <span className="text-xs text-muted">({role})</span>
                </td>
                <td className="text-right tabular-num font-mono">${formatNumber(price)}</td>
                <td className="text-right tabular-num font-mono">{formatNumber(qty)}</td>
                <td className="text-right tabular-num font-mono">{formatNumber(remaining)}</td>
                <td>
                  <span className={`badge ${state === 'WORKING' ? 'badge-warn' : 'badge-neutral'}`}>
                    {state}
                  </span>
                </td>
                <td className="text-right">
                  {onCancel && state === 'WORKING' && (
                    <button
                      type="button"
                      className="btn btn-danger btn-sm"
                      onClick={() => onCancel(row)}
                    >
                      Cancel
                    </button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function FillsTable({items}: {items: RecordData[]}) {
  if (!items.length) {
    return (
      <div className="empty-state-box">
        <p className="empty-title">No Fills Recorded</p>
        <p className="empty-subtext">No historical trades or executions recorded in this query.</p>
      </div>
    );
  }

  return (
    <div className="clean-table-wrapper">
      <table className="clean-table">
        <thead>
          <tr>
            <th scope="col">Time</th>
            <th scope="col">Contract</th>
            <th scope="col">Side</th>
            <th scope="col" className="text-right">Fill Price</th>
            <th scope="col" className="text-right">Quantity</th>
            <th scope="col" className="text-right">Realized P&amp;L</th>
            <th scope="col" className="text-right">Fee</th>
          </tr>
        </thead>
        <tbody>
          {items.map((row, i) => {
            const time = row.filled_at ? formatDateTime(String(row.filled_at)) : 'Recent';
            const sym = String(row.symbol || 'UNKNOWN');
            const dir = String(row.direction || 'LONG').toUpperCase();
            const price = String(row.price || '0');
            const qty = String(row.quantity || '0');
            const pnl = Number(row.realized_pnl || 0);
            const fee = String(row.fee || '0');
            const feeCcy = String(row.fee_ccy || 'USDT');

            return (
              <tr key={String(row.id ?? i)}>
                <td className="text-muted text-xs tabular-num">{time}</td>
                <td className="font-bold">{sym}</td>
                <td>
                  <span className={`badge ${dir === 'LONG' ? 'badge-positive' : 'badge-danger'}`}>
                    {dir}
                  </span>
                </td>
                <td className="text-right tabular-num font-mono">${formatNumber(price)}</td>
                <td className="text-right tabular-num font-mono">{formatNumber(qty)}</td>
                <td className={`text-right tabular-num font-mono font-bold ${pnl >= 0 ? 'text-success' : 'text-danger'}`}>
                  {pnl !== 0 ? `${pnl >= 0 ? '+' : ''}$${formatNumber(pnl.toFixed(2))}` : '—'}
                </td>
                <td className="text-right tabular-num font-mono text-muted text-xs">
                  {formatNumber(fee)} {feeCcy}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function SignalsTable({items}: {items: RecordData[]}) {
  if (!items.length) {
    return (
      <div className="empty-state-box">
        <p className="empty-title">Signals Inbox Empty</p>
        <p className="empty-subtext">No incoming Telegram signals recorded in this window.</p>
      </div>
    );
  }

  return (
    <div className="clean-table-wrapper">
      <table className="clean-table">
        <thead>
          <tr>
            <th scope="col">Received</th>
            <th scope="col">Channel</th>
            <th scope="col">Setup / Message</th>
            <th scope="col">Symbol</th>
            <th scope="col">Side</th>
            <th scope="col">Status</th>
            <th scope="col" className="text-right">Action</th>
          </tr>
        </thead>
        <tbody>
          {items.map((row, i) => {
            const time = row.received_at ? formatDateTime(String(row.received_at)) : 'Just now';
            const channel = String(row.channel_id || '@channel');
            const text = String(row.raw_text || 'No text content');
            const sym = String(row.symbol || '—');
            const dir = row.direction ? String(row.direction).toUpperCase() : null;
            const state = String(row.intake_state || row.state || 'PARSED');

            return (
              <tr key={String(row.id ?? i)}>
                <td className="text-muted text-xs tabular-num">{time}</td>
                <td className="font-mono text-xs text-secondary">{channel}</td>
                <td className="text-wrap" style={{maxWidth: '360px'}}>
                  <div className="signal-snippet">{text}</div>
                </td>
                <td className="font-bold">{sym}</td>
                <td>
                  {dir ? (
                    <span className={`badge ${dir === 'LONG' ? 'badge-positive' : 'badge-danger'}`}>
                      {dir}
                    </span>
                  ) : (
                    '—'
                  )}
                </td>
                <td>
                  <span className={`badge ${state === 'PARSED' || state === 'ADMITTED' ? 'badge-positive' : 'badge-neutral'}`}>
                    {state}
                  </span>
                </td>
                <td className="text-right">
                  <Link
                    to={`/signals/${encodeURIComponent(String(row.id || ''))}`}
                    className="btn btn-secondary btn-sm"
                  >
                    View Trace →
                  </Link>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function JobsTable({items}: {items: RecordData[]}) {
  if (!items.length) {
    return (
      <div className="empty-state-box">
        <p className="empty-title">No Command Jobs</p>
        <p className="empty-subtext">No durable command jobs have been executed yet.</p>
      </div>
    );
  }

  return (
    <div className="clean-table-wrapper">
      <table className="clean-table">
        <thead>
          <tr>
            <th scope="col">Time</th>
            <th scope="col">Command Action</th>
            <th scope="col">Risk Tier</th>
            <th scope="col">State</th>
            <th scope="col">Phase</th>
            <th scope="col">Actor</th>
            <th scope="col" className="text-right">Action</th>
          </tr>
        </thead>
        <tbody>
          {items.map((row, i) => {
            const time = row.created_at ? formatDateTime(String(row.created_at)) : 'Recent';
            const action = String(row.action || 'Unknown');
            const tier = Number(row.danger_tier ?? 0);
            const state = String(row.state || 'queued');
            const phase = String(row.phase || 'pending');
            const actor = String(row.actor_id || 'system');

            return (
              <tr key={String(row.id ?? i)}>
                <td className="text-muted text-xs tabular-num">{time}</td>
                <td className="font-mono font-bold">{action}</td>
                <td>
                  <span className={`badge ${tier >= 2 ? 'badge-warn' : 'badge-neutral'}`}>
                    Tier {tier}
                  </span>
                </td>
                <td>
                  <span
                    className={`badge ${
                      state === 'succeeded'
                        ? 'badge-positive'
                        : state === 'failed'
                        ? 'badge-danger'
                        : 'badge-warn'
                    }`}
                  >
                    {state}
                  </span>
                </td>
                <td className="text-xs text-secondary">{phase}</td>
                <td className="text-xs text-muted font-mono">{actor}</td>
                <td className="text-right">
                  <Link
                    to={`/audit/jobs/${encodeURIComponent(String(row.id || ''))}`}
                    className="btn btn-secondary btn-sm"
                  >
                    Details →
                  </Link>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// --------------------------------------------------------------------------
// Generic Fallback Table for Other Settings / Ops Collections
// --------------------------------------------------------------------------

export function GenericTable({
  section,
  items,
}: {
  section: string;
  items: RecordData[];
}) {
  if (!items.length) {
    return (
      <div className="empty-state-box">
        <p className="empty-title">No records</p>
        <p className="empty-subtext">No records observed in {human(section)}.</p>
      </div>
    );
  }

  // Derive column headers dynamically from first record keys
  const sample = items[0] || {};
  const displayKeys = Object.keys(sample).filter(
    (k) => !['resource_version', 'links', 'data', 'evidence'].includes(k)
  ).slice(0, 6);

  return (
    <div className="clean-table-wrapper">
      <table className="clean-table">
        <thead>
          <tr>
            <th scope="col">Identifier</th>
            {displayKeys.filter((k) => k !== 'id').map((k) => (
              <th scope="col" key={k}>
                {human(k)}
              </th>
            ))}
            <th scope="col" className="text-right">Details</th>
          </tr>
        </thead>
        <tbody>
          {items.map((row, i) => (
            <tr key={String(row.id ?? i)}>
              <td className="font-mono font-bold text-xs">
                {String(row.id || row.name || row.symbol || `Record #${i + 1}`)}
              </td>
              {displayKeys.filter((k) => k !== 'id').map((k) => (
                <td key={k} className="text-sm">
                  {typeof row[k] === 'boolean' ? (
                    <span className={`badge ${row[k] ? 'badge-positive' : 'badge-neutral'}`}>
                      {row[k] ? 'Yes' : 'No'}
                    </span>
                  ) : typeof row[k] === 'number' ? (
                    <span className="tabular-num font-mono">{formatNumber(Number(row[k]))}</span>
                  ) : String(row[k] || '—')}
                </td>
              ))}
              <td className="text-right">
                {row.id ? (
                  <Link
                    to={`/${section}/${encodeURIComponent(String(row.id))}`}
                    className="btn btn-secondary btn-sm"
                  >
                    View →
                  </Link>
                ) : (
                  '—'
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// --------------------------------------------------------------------------
// Primary Router for Domain Tables
// --------------------------------------------------------------------------

export function DomainTable({
  section,
  items,
  render,
  onAction,
}: {
  section: string;
  items: RecordData[];
  render: (value: unknown) => ReactNode;
  onAction?: (action: string, item: RecordData) => void;
}) {
  if (section === 'positions') {
    return <PositionsTable items={items} onClose={(item) => onAction?.('position.close', item)} />;
  }
  if (section === 'orders') {
    return <OrdersTable items={items} onCancel={(item) => onAction?.('order.cancel_exact', item)} />;
  }
  if (section === 'fills') {
    return <FillsTable items={items} />;
  }
  if (section === 'signals') {
    return <SignalsTable items={items} />;
  }
  if (section === 'jobs') {
    return <JobsTable items={items} />;
  }

  return <GenericTable section={section} items={items} />;
}

// --------------------------------------------------------------------------
// Domain Detail Card Stack
// --------------------------------------------------------------------------

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
    return <div className="card">{render(data)}</div>;
  }

  const record = data as RecordData;

  return (
    <div className="detail-container">
      <div className="card">
        <div className="card-header">
          <div>
            <span className="eyebrow">{section.toUpperCase()} RECORD</span>
            <h2>{String(record.symbol || record.action || record.id || 'Resource Detail')}</h2>
          </div>
          <Link to={`/${section.split('/')[0]}`} className="btn btn-secondary btn-sm">
            ← Return to list
          </Link>
        </div>

        <dl className="record-grid">
          {Object.entries(record)
            .filter(([k]) => !['linked', 'data', 'evidence', 'items'].includes(k))
            .map(([k, v]) => (
              <div key={k} className="record-field">
                <dt>{human(k)}</dt>
                <dd>{render(v)}</dd>
              </div>
            ))}
        </dl>
      </div>

      {Boolean(section === 'signals' && record.raw_text) && (
        <div className="card">
          <div className="card-header">
            <span className="eyebrow">IMMUTABLE TELEGRAM MESSAGE</span>
            <h2>Original Text</h2>
          </div>
          <blockquote className="raw-message-body">{String(record.raw_text)}</blockquote>
        </div>
      )}

      {Boolean(record.linked && isObjectRecord(record.linked)) && (
        <div className="card">
          <div className="card-header">
            <span className="eyebrow">LINKED TRACE TIMELINE</span>
            <h2>Related Events &amp; Transitions</h2>
          </div>
          <Value value={record.linked} />
        </div>
      )}
    </div>
  );
}
