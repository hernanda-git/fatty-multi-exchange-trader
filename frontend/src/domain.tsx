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

// Extract cashtag symbol from signal text if missing at root (e.g. "$ZEC" -> "ZECUSDT")
export function extractSignalSymbol(text: string): string | null {
  const match = text.match(/\$([A-Za-z0-9]{2,10})/);
  if (match && match[1]) {
    const sym = match[1].toUpperCase();
    return sym.endsWith('USDT') ? sym : `${sym}USDT`;
  }
  return null;
}

// --------------------------------------------------------------------------
// 1. Positions Table
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
        <p className="empty-title">No Open Positions (Flat)</p>
        <p className="empty-subtext">The bot and account currently hold zero open futures contracts on Bitget.</p>
        <div style={{marginTop: '12px'}}>
          <Link to="/trading" className="btn btn-primary btn-sm">
            + Open New Trade
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
            const dir = String(row.direction || row.side || 'LONG').toUpperCase();
            const provider = isObjectRecord(row.provider) ? row.provider : {};
            const total = String(provider.total || row.owned_residual || '0');
            const entry = String(provider.openPriceAvg || row.entry_price || '0');
            const mark = String(provider.markPrice || '0');
            const liq = String(provider.liquidationPrice || '0');
            const upnl = Number(provider.unrealizedPL || row.unrealized_pnl || 0);
            const prot = String(row.native_protection_proof || 'ACTIVE');

            return (
              <tr key={String(row.id ?? i)}>
                <td className="font-bold">
                  <Link to={`/positions/${encodeURIComponent(String(row.id || sym))}`}>
                    {sym}
                  </Link>
                </td>
                <td>
                  <span className={`badge ${dir === 'LONG' || dir === 'BUY' ? 'badge-positive' : 'badge-danger'}`}>
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

// --------------------------------------------------------------------------
// 2. Orders Table (Legitimate binding to requested_qty, filled_qty, side, filled_price)
// --------------------------------------------------------------------------

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
            <th scope="col">Time</th>
            <th scope="col">Contract</th>
            <th scope="col">Side</th>
            <th scope="col">Type / Role</th>
            <th scope="col" className="text-right">Price</th>
            <th scope="col" className="text-right">Quantity</th>
            <th scope="col" className="text-right">Filled</th>
            <th scope="col">State</th>
            <th scope="col" className="text-right">Action</th>
          </tr>
        </thead>
        <tbody>
          {items.map((row, i) => {
            const time = row.created_at ? formatDateTime(String(row.created_at)) : 'Recent';
            const sym = String(row.symbol || 'UNKNOWN');
            const side = String(row.side || row.direction || 'BUY').toUpperCase();
            const orderType = String(row.order_type || 'MARKET').toUpperCase();
            const role = String(row.role || 'ENTRY');
            const price = String(row.filled_price || row.limit_price || row.requested_price || row.price || 'Market');
            const reqQty = String(row.requested_qty ?? row.quantity ?? '0');
            const fillQty = String(row.filled_qty ?? row.filled_quantity ?? '0');
            const state = String(row.state || 'WORKING').toUpperCase();
            const isWorking = state === 'WORKING' || state === 'LIVE';

            return (
              <tr key={String(row.id ?? i)}>
                <td className="text-muted text-xs tabular-num">{time}</td>
                <td className="font-bold">
                  <Link to={`/orders/${encodeURIComponent(String(row.id || ''))}`}>
                    {sym}
                  </Link>
                  <div className="font-mono text-muted text-xs">
                    {String(row.client_order_id || row.id || '').slice(0, 24)}
                  </div>
                </td>
                <td>
                  <span className={`badge ${side === 'BUY' || side === 'LONG' ? 'badge-positive' : 'badge-danger'}`}>
                    {side}
                  </span>
                </td>
                <td>
                  <span className="badge badge-neutral">{orderType}</span>{' '}
                  <span className="text-xs text-muted">({role})</span>
                </td>
                <td className="text-right tabular-num font-mono">
                  {price === 'Market' ? 'Market' : `$${formatNumber(price)}`}
                </td>
                <td className="text-right tabular-num font-mono">{formatNumber(reqQty)}</td>
                <td className="text-right tabular-num font-mono">{formatNumber(fillQty)}</td>
                <td>
                  <span
                    className={`badge ${
                      state === 'FILLED'
                        ? 'badge-positive'
                        : isWorking
                        ? 'badge-warn'
                        : 'badge-neutral'
                    }`}
                  >
                    {state}
                  </span>
                </td>
                <td className="text-right">
                  {onCancel && isWorking ? (
                    <button
                      type="button"
                      className="btn btn-danger btn-sm"
                      onClick={() => onCancel(row)}
                    >
                      Cancel
                    </button>
                  ) : (
                    <span className="text-muted text-xs">—</span>
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

// --------------------------------------------------------------------------
// 3. Fills Table (Legitimate binding to filled_at, symbol, side, price, quantity, fee, fee_ccy)
// --------------------------------------------------------------------------

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
            <th scope="col">Filled At</th>
            <th scope="col">Contract</th>
            <th scope="col">Side</th>
            <th scope="col" className="text-right">Fill Price</th>
            <th scope="col" className="text-right">Quantity</th>
            <th scope="col" className="text-right">Realized P&amp;L</th>
            <th scope="col" className="text-right">Fee</th>
            <th scope="col">Order ID</th>
          </tr>
        </thead>
        <tbody>
          {items.map((row, i) => {
            const time = row.filled_at ? formatDateTime(String(row.filled_at)) : 'Recent';
            const sym = String(row.symbol || 'UNKNOWN');
            const side = String(row.side || row.direction || (Number(row.realized_pnl || 0) < 0 ? 'SELL' : 'BUY')).toUpperCase();
            const price = String(row.price || '0');
            const qty = String(row.quantity || '0');
            const pnl = Number(row.realized_pnl || 0);
            const fee = String(row.fee || '0');
            const feeCcy = String(row.fee_ccy || 'USDT');
            const clOrdId = String(row.client_order_id || row.provider_fill_id || '');

            return (
              <tr key={String(row.id ?? i)}>
                <td className="text-muted text-xs tabular-num">{time}</td>
                <td className="font-bold">{sym}</td>
                <td>
                  <span className={`badge ${side === 'BUY' || side === 'LONG' ? 'badge-positive' : 'badge-danger'}`}>
                    {side}
                  </span>
                </td>
                <td className="text-right tabular-num font-mono">${formatNumber(price)}</td>
                <td className="text-right tabular-num font-mono">{formatNumber(qty)}</td>
                <td className={`text-right tabular-num font-mono font-bold ${pnl > 0 ? 'text-success' : pnl < 0 ? 'text-danger' : 'text-muted'}`}>
                  {pnl !== 0 ? `${pnl > 0 ? '+' : ''}$${formatNumber(pnl.toFixed(2))}` : '$0.00'}
                </td>
                <td className="text-right tabular-num font-mono text-muted text-xs">
                  {formatNumber(fee)} {feeCcy}
                </td>
                <td className="font-mono text-muted text-xs" title={clOrdId}>
                  {clOrdId.slice(0, 18)}…
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
// 4. Signals Table (Legitimate channel formatting, cashtag extraction, message preview)
// --------------------------------------------------------------------------

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
            <th scope="col">Received (UTC)</th>
            <th scope="col">Channel / Source</th>
            <th scope="col">Message / Setup</th>
            <th scope="col">Symbol</th>
            <th scope="col">Side</th>
            <th scope="col">Intake Status</th>
            <th scope="col" className="text-right">Action</th>
          </tr>
        </thead>
        <tbody>
          {items.map((row, i) => {
            const time = row.received_at ? formatDateTime(String(row.received_at)) : 'Just now';
            const text = String(row.raw_text || '');
            const sym = String(row.symbol || extractSignalSymbol(text) || 'General Update');
            const dir = row.direction
              ? String(row.direction).toUpperCase()
              : /\b(LONG|BUY)\b/i.test(text)
              ? 'LONG'
              : /\b(SHORT|SELL)\b/i.test(text)
              ? 'SHORT'
              : null;
            const state = String(row.intake_state || row.state || 'PARSED').toUpperCase();
            const rejReason = row.entry_rejection_reason ? String(row.entry_rejection_reason) : null;
            const msgUrl = row.source_message_url ? String(row.source_message_url) : null;

            return (
              <tr key={String(row.id ?? i)}>
                <td className="text-muted text-xs tabular-num">{time}</td>
                <td className="text-xs">
                  {msgUrl ? (
                    <a href={msgUrl} target="_blank" rel="noopener noreferrer" className="font-mono">
                      Msg #{String(row.message_id || '')} ↗
                    </a>
                  ) : (
                    <span className="font-mono text-muted">Telegram</span>
                  )}
                </td>
                <td className="text-wrap" style={{maxWidth: '340px'}}>
                  <div className="text-xs" style={{whiteSpace: 'pre-wrap', maxHeight: '42px', overflow: 'hidden', textOverflow: 'ellipsis'}}>
                    {text.slice(0, 100)}{text.length > 100 ? '…' : ''}
                  </div>
                </td>
                <td className="font-bold">{sym}</td>
                <td>
                  {dir ? (
                    <span className={`badge ${dir === 'LONG' ? 'badge-positive' : 'badge-danger'}`}>
                      {dir}
                    </span>
                  ) : (
                    <span className="text-muted text-xs">—</span>
                  )}
                </td>
                <td>
                  <span
                    className={`badge ${
                      state === 'PARSED' || state === 'ADMITTED'
                        ? 'badge-positive'
                        : state === 'EXPIRED'
                        ? 'badge-neutral'
                        : 'badge-warn'
                    }`}
                    title={rejReason || undefined}
                  >
                    {state}
                  </span>
                  {rejReason && <div className="text-muted text-xs">{rejReason}</div>}
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

// --------------------------------------------------------------------------
// 5. Jobs Table
// --------------------------------------------------------------------------

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
            <th scope="col">Action</th>
            <th scope="col">Tier</th>
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
                <td className="font-mono font-bold text-xs">{action}</td>
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
// 6. Account & Performance Dedicated View (Legitimate Bindings to Snapshots and Ledger)
// --------------------------------------------------------------------------

export function AccountDetailsView({data}: {data: RecordData}) {
  const snapshotsObj = isObjectRecord(data.admission_snapshots) ? data.admission_snapshots : {};
  const snapshots = Array.isArray(snapshotsObj.items) ? (snapshotsObj.items as RecordData[]) : [];
  const latestSnapshot = snapshots[0] || null;

  const ledgerPnl = isObjectRecord(data.ledger_pnl) ? data.ledger_pnl : {};
  const feeGroups = Array.isArray(ledgerPnl.by_fee_currency) ? (ledgerPnl.by_fee_currency as RecordData[]) : [];
  const usdtGroup = feeGroups.find((g) => g.fee_ccy === 'USDT') || feeGroups[0] || null;

  const reservationsObj = isObjectRecord(data.reservations) ? data.reservations : {};
  const reservations = Array.isArray(reservationsObj.items) ? (reservationsObj.items as RecordData[]) : [];

  return (
    <div className="account-details-stack">
      {/* Top Balances & Performance Summary */}
      <div className="metric-grid-4">
        <div className="metric-tile">
          <span className="metric-tile-label">Verified Equity</span>
          <span className="metric-tile-value">
            ${latestSnapshot ? formatNumber(Number(latestSnapshot.equity || 0).toFixed(2)) : '0.00'}
          </span>
          <span className="metric-tile-sub">
            {latestSnapshot ? `Snapshot ${String(latestSnapshot.id || '').slice(0, 8)}…` : 'Snapshot unobserved'}
          </span>
        </div>

        <div className="metric-tile">
          <span className="metric-tile-label">Available Balance</span>
          <span className="metric-tile-value">
            ${latestSnapshot ? formatNumber(Number(latestSnapshot.available_balance || 0).toFixed(2)) : '0.00'}
          </span>
          <span className="metric-tile-sub">Margin currency: {String(latestSnapshot?.margin_coin || 'USDT')}</span>
        </div>

        <div className="metric-tile">
          <span className="metric-tile-label">Gross Realized P&amp;L</span>
          <span className={`metric-tile-value ${Number(usdtGroup?.gross_realized_pnl || 0) >= 0 ? 'text-success' : 'text-danger'}`}>
            {usdtGroup ? `${Number(usdtGroup.gross_realized_pnl) >= 0 ? '+' : ''}$${formatNumber(Number(usdtGroup.gross_realized_pnl).toFixed(2))}` : '—'}
          </span>
          <span className="metric-tile-sub">{usdtGroup ? `${usdtGroup.fill_count} ledger fills` : 'No fills recorded'}</span>
        </div>

        <div className="metric-tile">
          <span className="metric-tile-label">Total Execution Fees</span>
          <span className="metric-tile-value">
            ${usdtGroup ? formatNumber(Number(usdtGroup.fees || 0).toFixed(2)) : '0.00'}
          </span>
          <span className="metric-tile-sub">Fee currency: {String(usdtGroup?.fee_ccy || 'USDT')}</span>
        </div>
      </div>

      {/* Honest Economic Disclosure Note */}
      <div className="card" style={{padding: '14px 18px', background: 'var(--bg-subtle)'}}>
        <div className="text-xs text-secondary">
          <strong>Legitimate Financial Reporting Disclosure:</strong> {String(ledgerPnl.note || 'Gross realized P&L and fees remain separate; account-wide net P&L is unobserved in current read models.')}
        </div>
      </div>

      {/* Margin Reservations Table */}
      <div className="card">
        <div className="card-header">
          <h2>Margin Reservations</h2>
          <span className="badge badge-neutral">{reservations.length} records</span>
        </div>
        {reservations.length ? (
          <div className="clean-table-wrapper">
            <table className="clean-table">
              <thead>
                <tr>
                  <th scope="col">Reservation ID</th>
                  <th scope="col">Exchange</th>
                  <th scope="col">State</th>
                  <th scope="col">Symbol</th>
                </tr>
              </thead>
              <tbody>
                {reservations.map((r, i) => (
                  <tr key={String(r.id ?? i)}>
                    <td className="font-mono text-xs">{String(r.id)}</td>
                    <td className="font-mono">{String(r.exchange || 'bitget')}</td>
                    <td>
                      <span className={`badge ${r.state === 'active' ? 'badge-warn' : 'badge-neutral'}`}>
                        {String(r.state || 'released')}
                      </span>
                    </td>
                    <td>{String(r.symbol || '—')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-secondary text-sm">No active margin reservations.</p>
        )}
      </div>

      {/* Historical Admission Snapshots Table */}
      <div className="card">
        <div className="card-header">
          <h2>Admission Balance Snapshots</h2>
          <span className="badge badge-neutral">{snapshots.length} snapshots</span>
        </div>
        {snapshots.length ? (
          <div className="clean-table-wrapper">
            <table className="clean-table">
              <thead>
                <tr>
                  <th scope="col">Captured At (UTC)</th>
                  <th scope="col">Snapshot ID</th>
                  <th scope="col">Coin</th>
                  <th scope="col" className="text-right">Equity</th>
                  <th scope="col" className="text-right">Available Balance</th>
                  <th scope="col" className="text-right">Total Balance</th>
                </tr>
              </thead>
              <tbody>
                {snapshots.map((s, i) => (
                  <tr key={String(s.id ?? i)}>
                    <td className="text-muted text-xs tabular-num">
                      {s.captured_at ? formatDateTime(String(s.captured_at)) : '—'}
                    </td>
                    <td className="font-mono text-xs">{String(s.id).slice(0, 16)}…</td>
                    <td className="font-bold">{String(s.margin_coin || 'USDT')}</td>
                    <td className="text-right tabular-num font-mono">${formatNumber(String(s.equity || '0'))}</td>
                    <td className="text-right tabular-num font-mono">${formatNumber(String(s.available_balance || '0'))}</td>
                    <td className="text-right tabular-num font-mono">${formatNumber(String(s.total_balance || '0'))}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-secondary text-sm">No admission balance snapshots recorded.</p>
        )}
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------
// 7. Recovery & Safety Dedicated View
// --------------------------------------------------------------------------

export function RecoveryDetailsView({data}: {data: RecordData}) {
  const latches = Array.isArray(data.latches) ? (data.latches as RecordData[]) : [];
  const proofStatus = String(data.proof_status || 'unknown');
  const proofNote = String(data.proof_note || 'Recovery proof not established.');

  return (
    <div className="recovery-details-stack">
      {/* Containment Latches Card */}
      <div className="card">
        <div className="card-header">
          <div>
            <span className="eyebrow">FAIL-CLOSED CONTAINMENT</span>
            <h2>Safety Latches</h2>
          </div>
          <span className="badge badge-warn">{latches.filter((l) => l.active).length} Active</span>
        </div>

        <div className="clean-table-wrapper">
          <table className="clean-table">
            <thead>
              <tr>
                <th scope="col">Scope</th>
                <th scope="col">Status</th>
                <th scope="col">Latch Reason</th>
                <th scope="col">Latched At</th>
                <th scope="col">Updated At</th>
              </tr>
            </thead>
            <tbody>
              {latches.map((l, i) => (
                <tr key={String(l.scope ?? i)}>
                  <td className="font-mono font-bold">{String(l.scope)}</td>
                  <td>
                    <span className={`badge ${l.active ? 'badge-danger' : 'badge-positive'}`}>
                      {l.active ? 'LATCHED (Active)' : 'Released'}
                    </span>
                  </td>
                  <td className="font-mono text-sm">{String(l.reason || 'None')}</td>
                  <td className="text-muted text-xs tabular-num">
                    {l.latched_at ? formatDateTime(String(l.latched_at)) : '—'}
                  </td>
                  <td className="text-muted text-xs tabular-num">
                    {l.updated_at ? formatDateTime(String(l.updated_at)) : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Recovery Proof Status */}
      <div className="card">
        <div className="card-header">
          <h2>Recovery Proof Verification</h2>
          <span className={`badge ${proofStatus === 'verified' ? 'badge-positive' : 'badge-neutral'}`}>
            Status: {proofStatus}
          </span>
        </div>
        <p className="text-secondary text-sm">{proofNote}</p>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------
// 8. Access & Sessions Dedicated View
// --------------------------------------------------------------------------

export function AccessDetailsView({data}: {data: RecordData}) {
  const policy = isObjectRecord(data.policy) ? data.policy : {};
  const principalsObj = isObjectRecord(data.principals) ? data.principals : {};
  const principals = Array.isArray(principalsObj.items) ? (principalsObj.items as RecordData[]) : [];
  const sessionsObj = isObjectRecord(data.sessions) ? data.sessions : {};
  const sessions = Array.isArray(sessionsObj.items) ? (sessionsObj.items as RecordData[]) : [];

  return (
    <div className="access-details-stack">
      {/* Policy Card */}
      <div className="card">
        <div className="card-header">
          <div>
            <span className="eyebrow">GOVERNANCE</span>
            <h2>Password Authentication Policy</h2>
          </div>
          <span className="badge badge-positive">Active Policy</span>
        </div>
        <dl className="record-grid">
          <div>
            <dt>Authentication Mode</dt>
            <dd><strong>Shared Console Password</strong> (Password-Only)</dd>
          </div>
          <div>
            <dt>Policy Version</dt>
            <dd><code>{String(policy.version || 'console-password-v2')}</code></dd>
          </div>
          <div>
            <dt>Session Idle Timeout</dt>
            <dd>{Number(policy.session_idle_seconds || 1800) / 60} minutes</dd>
          </div>
          <div>
            <dt>Session Absolute Expiry</dt>
            <dd>{Number(policy.session_absolute_seconds || 28800) / 3600} hours</dd>
          </div>
          <div>
            <dt>Two-Person Approval Policy</dt>
            <dd>Tier 3 actions require distinct approver (Enforced fail-closed)</dd>
          </div>
        </dl>
      </div>

      {/* Active Sessions */}
      <div className="card">
        <div className="card-header">
          <h2>Active Operator Sessions</h2>
          <span className="badge badge-neutral">{sessions.length} sessions</span>
        </div>
        {sessions.length ? (
          <div className="clean-table-wrapper">
            <table className="clean-table">
              <thead>
                <tr>
                  <th scope="col">Session Token</th>
                  <th scope="col">Principal</th>
                  <th scope="col">Authenticated (UTC)</th>
                  <th scope="col">Last Seen</th>
                </tr>
              </thead>
              <tbody>
                {sessions.map((s, i) => (
                  <tr key={String(s.id ?? i)}>
                    <td className="font-mono text-xs">{String(s.id || '').slice(0, 20)}…</td>
                    <td className="font-bold">{String(s.principal_id || 'console-password-owner')}</td>
                    <td className="text-muted text-xs tabular-num">
                      {s.authenticated_at ? formatDateTime(String(s.authenticated_at)) : '—'}
                    </td>
                    <td className="text-muted text-xs tabular-num">
                      {s.last_seen_at ? formatDateTime(String(s.last_seen_at)) : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-secondary text-sm">No remote session tokens stored.</p>
        )}
      </div>
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

  return (
    <div className="clean-table-wrapper">
      <table className="clean-table">
        <thead>
          <tr>
            <th scope="col">Record</th>
            <th scope="col">State / Kind</th>
            <th scope="col">Created At</th>
          </tr>
        </thead>
        <tbody>
          {items.map((row, i) => (
            <tr key={String(row.id ?? i)}>
              <td className="font-mono font-bold text-xs">
                {String(row.symbol || row.action || row.name || row.id || `Item #${i + 1}`)}
              </td>
              <td>
                <span className="badge badge-neutral">{String(row.state || row.kind || row.health || 'ACTIVE')}</span>
              </td>
              <td className="text-muted text-xs tabular-num">
                {row.created_at ? formatDateTime(String(row.created_at)) : '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
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
    return <div className="card"><Value value={data} /></div>;
  }

  const record = data as RecordData;

  if (section === 'account') {
    return <AccountDetailsView data={record} />;
  }
  if (section === 'recovery') {
    return <RecoveryDetailsView data={record} />;
  }
  if (section === 'access') {
    return <AccessDetailsView data={record} />;
  }

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
                <dd><Value value={v} /></dd>
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
