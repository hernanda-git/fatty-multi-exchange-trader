import React from 'react';
import {Link} from '../nav';
import {Value, StatusBadge, formatDateTime, formatNumber} from '../primitives';
import {DomainTable, DomainDetail} from '../domain';
import {type Envelope, type RecordData, isObjectRecord} from '../types';

export function OverviewPage({
  envelope,
  safety,
  readSection,
  resourceId,
  render,
}: {
  envelope: Envelope | null;
  safety: Envelope | null;
  readSection: string;
  resourceId: string;
  render: (value: unknown) => React.ReactNode;
}) {
  const isAccountView = readSection.startsWith('account');
  const safetyRecord = safety && isObjectRecord(safety.data) ? safety.data : null;
  const safetyData = safetyRecord ?? {};
  const data = envelope ? envelope.data : null;
  const items = isObjectRecord(data) && Array.isArray(data.items) ? data.items.filter(isObjectRecord) : [];

  // Parse safety & overview facts safely
  const latches = Array.isArray(safetyData.latches) ? safetyData.latches : [];
  const activeLatches = latches.filter((l: unknown) => isObjectRecord(l) && l.active === true);
  const stream = isObjectRecord(safetyData.stream) ? safetyData.stream : null;
  const clock = isObjectRecord(safetyData.clock) ? safetyData.clock : null;
  const effectiveConfig = isObjectRecord(safetyData.effective_config) ? safetyData.effective_config : null;
  if (isAccountView) {
    return (
      <div className="page-content account-page">
        <div className="section-intro">
          <h2>Account & Economic Observations</h2>
          <p className="text-secondary">
            Authoritative provider balances, margin reservations, and historical fill economics.
            Account-wide net P&amp;L is unobserved in current read models; recorded gross realized P&amp;L and fees are displayed truthfully.
          </p>
        </div>

        {resourceId ? (
          <DomainDetail section={readSection} data={data} render={render} />
        ) : items.length > 0 ? (
          <DomainTable section={readSection} items={items} render={render} />
        ) : (
          <DomainDetail section="account" data={data} render={render} />
        )}
      </div>
    );
  }

  return (
    <div className="page-content overview-page">
      {/* Top Operating Status Card */}
      <section className="card status-hero-card">
        <div className="hero-header">
          <div>
            <span className="eyebrow">SYSTEM STATUS</span>
            <h2>Operating Condition & Admission</h2>
          </div>
          <div className="hero-badges">
            <StatusBadge
              status={activeLatches.length > 0 ? 'danger' : 'warn'}
              label={activeLatches.length > 0 ? 'Containment Active' : 'Admission Gated'}
            />
          </div>
        </div>

        <div className="metric-strip">
          <div className="metric-card">
            <span className="metric-label">Admission Verdict</span>
            <span className="metric-value text-muted">Not Observed</span>
            <small className="metric-note">Independent latches &amp; risk gates enforce fail-closed</small>
          </div>

          <div className="metric-card">
            <span className="metric-label">Active Latches</span>
            <span className={`metric-value ${activeLatches.length > 0 ? 'text-danger' : 'text-success'}`}>
              {activeLatches.length}
            </span>
            <small className="metric-note">
              {activeLatches.length > 0 ? (
                <Link to="/recovery">Inspect {activeLatches.length} latch{activeLatches.length > 1 ? 'es' : ''} →</Link>
              ) : (
                'No active database latches'
              )}
            </small>
          </div>

          <div className="metric-card">
            <span className="metric-label">Protection Stream</span>
            <span className="metric-value">
              {stream ? (
                <StatusBadge
                  status={stream.state === 'running' || stream.state === 'connected' ? 'fresh' : 'warn'}
                  label={String(stream.state || 'Unknown')}
                />
              ) : (
                'Not observed'
              )}
            </span>
            <small className="metric-note">
              {stream && stream.observed_at ? `Observed ${formatDateTime(String(stream.observed_at))}` : 'No recent stream heartbeat'}
            </small>
          </div>

          <div className="metric-card">
            <span className="metric-label">Exchange Clock</span>
            <span className="metric-value">
              {clock ? (
                <StatusBadge
                  status={clock.healthy ? 'fresh' : 'warn'}
                  label={clock.healthy ? 'Synchronized' : 'Desynchronized'}
                />
              ) : (
                'Not observed'
              )}
            </span>
            <small className="metric-note">
              {clock && clock.skew_ms !== undefined ? `Skew: ${formatNumber(Number(clock.skew_ms))} ms` : 'Clock telemetry unavailable'}
            </small>
          </div>
        </div>
      </section>

      {/* Exposure & Account Summary */}
      <div className="grid-two-col">
        <section className="card">
          <div className="card-header">
            <div>
              <span className="eyebrow">PORTFOLIO</span>
              <h2>Open Positions &amp; Exposure</h2>
            </div>
            <Link to="/positions" className="btn btn-secondary btn-sm">Trading Desk →</Link>
          </div>
          <p className="text-secondary">
            Active market risk across Bitget contracts. Detailed liquidation and native protection proofs are inspected in Trading.
          </p>
          <div className="quick-access-box">
            <div className="box-item">
              <span className="box-title">Positions</span>
              <Link to="/positions" className="link-action">Inspect open contracts</Link>
            </div>
            <div className="box-item">
              <span className="box-title">Working Orders</span>
              <Link to="/orders" className="link-action">View orders &amp; fills</Link>
            </div>
            <div className="box-item">
              <span className="box-title">SL / TP Protection</span>
              <Link to="/protection" className="link-action">Review protection proofs</Link>
            </div>
          </div>
        </section>

        <section className="card">
          <div className="card-header">
            <div>
              <span className="eyebrow">ACTIVITY</span>
              <h2>Recent Signals &amp; Jobs</h2>
            </div>
            <Link to="/signals" className="btn btn-secondary btn-sm">Signals Inbox →</Link>
          </div>
          <p className="text-secondary">
            Verified intake revisions, dispatched trade instructions, and durable command audit logs.
          </p>
          <div className="quick-access-box">
            <div className="box-item">
              <span className="box-title">Telegram Signals</span>
              <Link to="/signals" className="link-action">Inspect immutable revisions</Link>
            </div>
            <div className="box-item">
              <span className="box-title">Durable Jobs</span>
              <Link to="/audit" className="link-action">Follow operations &amp; readback</Link>
            </div>
            <div className="box-item">
              <span className="box-title">Account &amp; Balances</span>
              <Link to="/account" className="link-action">Inspect margin &amp; PnL</Link>
            </div>
          </div>
        </section>
      </div>

      {/* Snapshot domain details */}
      <section className="card">
        <div className="card-header">
          <div>
            <span className="eyebrow">EVIDENCE PANELS</span>
            <h2>Independent Verification Domains</h2>
          </div>
        </div>
        <DomainDetail section="overview" data={safetyData} render={render} />
      </section>
    </div>
  );
}
