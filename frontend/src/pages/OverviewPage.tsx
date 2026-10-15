import React from 'react';
import {Link} from '../nav';
import {StatusBadge, formatDateTime, formatNumber} from '../primitives';
import {PositionsTable, SignalsTable, DomainDetail} from '../domain';
import {type Envelope, type RecordData, isObjectRecord} from '../types';

export function OverviewPage({
  envelope,
  safety,
  readSection,
  resourceId,
  onOpenAction,
  render,
}: {
  envelope: Envelope | null;
  safety: Envelope | null;
  readSection: string;
  resourceId: string;
  onOpenAction?: (action: string, initialArgs?: RecordData, initialTargets?: string[]) => void;
  render: (value: unknown) => React.ReactNode;
}) {
  const isAccountView = readSection.startsWith('account');
  const safetyRecord = safety && isObjectRecord(safety.data) ? safety.data : null;
  const safetyData = safetyRecord ?? {};
  const data = envelope ? envelope.data : null;
  const items = isObjectRecord(data) && Array.isArray(data.items) ? data.items.filter(isObjectRecord) : [];

  // Extract financial & safety figures
  const provider = isObjectRecord(safetyData.provider) ? safetyData.provider : {};
  const equity = String(provider.equity || '0.00');
  const available = String(provider.available_balance || '0.00');
  const upnl = Number(provider.unrealized_pnl || 0);

  const latches = Array.isArray(safetyData.latches) ? safetyData.latches : [];
  const activeLatches = latches.filter((l: unknown) => isObjectRecord(l) && l.active === true);
  const stream = isObjectRecord(safetyData.stream) ? safetyData.stream : null;
  const clock = isObjectRecord(safetyData.clock) ? safetyData.clock : null;

  if (isAccountView) {
    return (
      <div className="page-content account-page">
        <div className="card">
          <div className="card-header">
            <div>
              <span className="eyebrow">PORTFOLIO ECONOMICS</span>
              <h2>Balances &amp; Ledger Performance</h2>
            </div>
          </div>
          <p className="text-secondary text-sm" style={{marginBottom: '16px'}}>
            Verified provider balances and ledger-recorded fill economics. Account-wide net P&amp;L is
            unobserved in current read models; recorded gross realized P&amp;L and fees are displayed truthfully.
          </p>

          <DomainDetail section="account" data={safetyData} render={render} />
        </div>
      </div>
    );
  }

  return (
    <div className="page-content overview-page">
      {/* 4 Clean Executive Metric Tiles */}
      <div className="metric-grid-4">
        <div className="metric-tile">
          <span className="metric-tile-label">Account Equity</span>
          <span className="metric-tile-value">${formatNumber(equity)}</span>
          <span className="metric-tile-sub">Available: ${formatNumber(available)} USDT</span>
        </div>

        <div className="metric-tile">
          <span className="metric-tile-label">Unrealized P&amp;L</span>
          <span className={`metric-tile-value ${upnl >= 0 ? 'text-success' : 'text-danger'}`}>
            {upnl >= 0 ? '+' : ''}${formatNumber(upnl.toFixed(2))}
          </span>
          <span className="metric-tile-sub">Across open futures contracts</span>
        </div>

        <div className="metric-tile">
          <span className="metric-tile-label">Admission State</span>
          <span className="metric-tile-value" style={{fontSize: '16px', display: 'flex', alignItems: 'center', gap: '6px'}}>
            <StatusBadge
              status={activeLatches.length > 0 ? 'danger' : 'warn'}
              label={activeLatches.length > 0 ? 'Containment Latched' : 'Admission Fail-Closed'}
            />
          </span>
          <span className="metric-tile-sub">
            {activeLatches.length > 0 ? `${activeLatches.length} active incident latch` : 'No positive admission asserted'}
          </span>
        </div>

        <div className="metric-tile">
          <span className="metric-tile-label">Exchange Connection</span>
          <span className="metric-tile-value" style={{fontSize: '16px', display: 'flex', alignItems: 'center', gap: '6px'}}>
            <StatusBadge
              status={clock && clock.healthy ? 'fresh' : 'warn'}
              label={clock && clock.healthy ? 'Synchronized' : 'Clock Check Pending'}
            />
          </span>
          <span className="metric-tile-sub">
            {clock && clock.skew_ms !== undefined ? `Clock skew: ${clock.skew_ms}ms` : 'Bitget V2 stream'}
          </span>
        </div>
      </div>

      {/* Active Positions Table on Overview */}
      <div className="card">
        <div className="card-header">
          <div>
            <span className="eyebrow">PORTFOLIO</span>
            <h2>Active Positions</h2>
          </div>
          <Link to="/trading" className="btn btn-primary btn-sm">
            + New Trade
          </Link>
        </div>
        <PositionsTable
          items={items.filter((i) => i.symbol && (i.direction || isObjectRecord(i.provider)))}
          onClose={(item) => onOpenAction?.('position.close', item)}
        />
      </div>

      {/* Quick Navigation Cards */}
      <div style={{display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '16px', marginTop: '16px'}}>
        <div className="card" style={{marginBottom: 0}}>
          <div className="card-header">
            <h2>Trading Desk</h2>
            <Link to="/positions" className="btn btn-secondary btn-sm">Open →</Link>
          </div>
          <p className="text-secondary text-sm">
            Manage open contracts, view working limit orders, audit fill ledger, and configure native SL/TP.
          </p>
        </div>

        <div className="card" style={{marginBottom: 0}}>
          <div className="card-header">
            <h2>Signal Stream</h2>
            <Link to="/signals" className="btn btn-secondary btn-sm">Open →</Link>
          </div>
          <p className="text-secondary text-sm">
            Monitor incoming Telegram setups, immutable text revisions, and execution dispatches.
          </p>
        </div>

        <div className="card" style={{marginBottom: 0}}>
          <div className="card-header">
            <h2>Safety &amp; Recovery</h2>
            <Link to="/recovery" className="btn btn-secondary btn-sm">Open →</Link>
          </div>
          <p className="text-secondary text-sm">
            Review incident containment, clock evidence, baseline exclusions, and recovery proofs.
          </p>
        </div>
      </div>
    </div>
  );
}
