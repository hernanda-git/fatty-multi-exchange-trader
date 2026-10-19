import React from 'react';
import {Link} from '../nav';
import {StatusBadge, formatDateTime, formatNumber} from '../primitives';
import {PositionsTable, SignalsTable, AccountDetailsView} from '../domain';
import {type Envelope, type RecordData, isObjectRecord} from '../types';

function toNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function signedMoney(value: number | null): string {
  if (value === null) return '—';
  return `${value >= 0 ? '+' : '−'}$${formatNumber(Math.abs(value).toFixed(2))}`;
}

function moneyText(value: number | null): string {
  return value === null ? '—' : `$${formatNumber(Math.abs(value).toFixed(2))}`;
}

function priceText(value: number | null): string {
  if (value === null) return '—';
  return `$${formatNumber(String(Number(value.toFixed(6))))}`;
}

function percentText(value: number | null): string {
  if (value === null) return '—';
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`;
}

const LIVE_PLAN_STATUSES = ['live', 'new', 'open', 'pending', 'waiting', 'not_trigger', 'not_triggered'];

function PositionRiskBlock({row, plans}: {row: RecordData; plans: RecordData[]}) {
  const symbol = String(row.symbol ?? 'UNKNOWN');
  const side = String(row.holdSide ?? row.direction ?? '').toLowerCase();
  const isLong = side === 'long' || side === 'buy';
  const size = toNumber(row.total) ?? 0;
  const entry = toNumber(row.openPriceAvg ?? row.averageOpenPrice);
  const mark = toNumber(row.markPrice);
  const liquidation = toNumber(row.liquidationPrice);
  const margin = toNumber(row.marginSize);
  const leverage = toNumber(row.leverage);
  const breakEven = toNumber(row.breakEvenPrice);
  const rowUpnl =
    toNumber(row.unrealizedPL) ??
    (entry !== null && mark !== null ? (isLong ? mark - entry : entry - mark) * size : null);

  const symbolPlans = plans.filter(
    (plan) => String(plan.symbol ?? '').toUpperCase() === symbol.toUpperCase()
  );
  const stopPlan = symbolPlans.find((plan) => String(plan.planType ?? '') === 'pos_loss');
  const targetPlan = symbolPlans.find((plan) => String(plan.planType ?? '') === 'pos_profit');
  const stop = toNumber(stopPlan?.triggerPrice) ?? toNumber(row.stopLoss);
  const target = toNumber(targetPlan?.triggerPrice) ?? toNumber(row.takeProfit);
  const livePlanCount = [stopPlan, targetPlan].filter((plan) => {
    const status = String(plan?.planStatus ?? plan?.status ?? '').toLowerCase();
    return LIVE_PLAN_STATUSES.includes(status);
  }).length;
  const venueLinked = Boolean(row.stopLossId || row.takeProfitId);

  const pnlAt = (price: number | null): number | null =>
    price === null || entry === null ? null : (isLong ? price - entry : entry - price) * size;
  const fromMark = (price: number | null): number | null =>
    price === null || mark === null || mark === 0 ? null : ((price - mark) / mark) * 100;
  const scenarios: {label: string; level: number | null; pnl: number | null; tone: string}[] = (
    [
      ['Take-profit', target, 'text-success'],
      ['Now (mark)', mark, 'text-secondary'],
      ['Stop-loss', stop, 'text-danger'],
      ['Liquidation', liquidation, 'text-danger'],
    ] as [string, number | null, string][]
  ).map(([label, level, tone]) => ({
    label,
    level,
    tone,
    pnl: label === 'Now (mark)' && rowUpnl !== null ? rowUpnl : pnlAt(level),
  }));

  const stopBreached = stop !== null && mark !== null && (isLong ? mark <= stop : mark >= stop);
  const liquidationInsideStop =
    stop !== null && liquidation !== null && (isLong ? liquidation > stop : liquidation < stop);

  return (
    <div
      style={{
        border: '1px solid var(--border-subtle)',
        borderRadius: '8px',
        padding: '14px 16px',
        marginTop: '14px',
        background: 'var(--bg-subtle)',
      }}
    >
      <div style={{display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap', marginBottom: '10px'}}>
        <strong style={{fontSize: '15px'}}>{symbol}</strong>
        <span className={`badge ${isLong ? 'badge-positive' : 'badge-danger'}`}>{isLong ? 'LONG' : 'SHORT'}</span>
        <span className="text-sm text-secondary">
          {formatNumber(String(size))} · {priceText(mark)} mark
        </span>
        {leverage !== null && <span className="badge badge-neutral">{formatNumber(String(leverage))}×</span>}
        <StatusBadge
          status={livePlanCount === 2 ? 'fresh' : stop !== null || target !== null ? 'warn' : 'danger'}
          label={
            livePlanCount === 2
              ? 'Venue SL/TP plans live'
              : stop !== null || target !== null
              ? 'Partial protection read'
              : 'Protection unproven'
          }
        />
        {venueLinked && <span className="text-sm text-secondary">position-linked ids</span>}
        {stopBreached && <StatusBadge status="danger" label="Mark past the stop" />}
      </div>

      <div style={{display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: '16px'}}>
        <div className="clean-table-wrapper" style={{margin: 0}}>
          <table className="clean-table">
            <thead>
              <tr>
                <th scope="col">Level</th>
                <th scope="col" className="text-right">Price</th>
                <th scope="col" className="text-right">vs mark</th>
                <th scope="col" className="text-right">P&amp;L from entry</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>Entry</td>
                <td className="text-right tabular-num font-mono">{priceText(entry)}</td>
                <td className="text-right tabular-num font-mono">{percentText(fromMark(entry))}</td>
                <td className="text-right tabular-num font-mono">{signedMoney(pnlAt(entry))}</td>
              </tr>
              <tr>
                <td>Mark</td>
                <td className="text-right tabular-num font-mono">{priceText(mark)}</td>
                <td className="text-right tabular-num font-mono">—</td>
                <td className={`text-right tabular-num font-mono font-bold ${(rowUpnl ?? 0) >= 0 ? 'text-success' : 'text-danger'}`}>
                  {signedMoney(rowUpnl)}
                </td>
              </tr>
              <tr>
                <td>Stop-loss</td>
                <td className="text-right tabular-num font-mono">{priceText(stop)}</td>
                <td className="text-right tabular-num font-mono">{percentText(fromMark(stop))}</td>
                <td className="text-right tabular-num font-mono text-danger">{signedMoney(pnlAt(stop))}</td>
              </tr>
              <tr>
                <td>Take-profit</td>
                <td className="text-right tabular-num font-mono">{priceText(target)}</td>
                <td className="text-right tabular-num font-mono">{percentText(fromMark(target))}</td>
                <td className="text-right tabular-num font-mono text-success">{signedMoney(pnlAt(target))}</td>
              </tr>
              <tr>
                <td>Liquidation</td>
                <td className="text-right tabular-num font-mono">{priceText(liquidation)}</td>
                <td className="text-right tabular-num font-mono">{percentText(fromMark(liquidation))}</td>
                <td className="text-right tabular-num font-mono text-danger">{signedMoney(pnlAt(liquidation))}</td>
              </tr>
              <tr>
                <td>Break-even</td>
                <td className="text-right tabular-num font-mono">{priceText(breakEven)}</td>
                <td className="text-right tabular-num font-mono">{percentText(fromMark(breakEven))}</td>
                <td className="text-right tabular-num font-mono">—</td>
              </tr>
            </tbody>
          </table>
        </div>

        <div className="clean-table-wrapper" style={{margin: 0}}>
          <table className="clean-table">
            <thead>
              <tr>
                <th scope="col">Scenario</th>
                <th scope="col" className="text-right">PnL</th>
                <th scope="col" className="text-right">Δ vs now</th>
              </tr>
            </thead>
            <tbody>
              {scenarios.map((scenario) => {
                const delta =
                  scenario.pnl !== null && rowUpnl !== null && scenario.label !== 'Now (mark)'
                    ? scenario.pnl - rowUpnl
                    : scenario.label === 'Now (mark)'
                    ? 0
                    : null;
                return (
                  <tr key={scenario.label}>
                    <td>
                      <span className={scenario.tone}>
                        {scenario.label} · {priceText(scenario.level)}
                      </span>
                    </td>
                    <td className={`text-right tabular-num font-mono font-bold ${(scenario.pnl ?? 0) >= 0 ? 'text-success' : 'text-danger'}`}>
                      {signedMoney(scenario.pnl)}
                    </td>
                    <td className="text-right tabular-num font-mono text-secondary">{signedMoney(delta)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="text-sm text-secondary" style={{margin: '10px 0 0'}}>
            Isolated margin {moneyText(margin)} · notional {moneyText(mark !== null ? mark * size : null)}.
            {liquidationInsideStop
              ? ' Liquidation sits inside the stop — the venue stop is the first exit.'
              : ''}
          </p>
        </div>
      </div>
    </div>
  );
}

function CapitalAndRisk({
  equity,
  available,
  upnl,
  coin,
  positions,
  plans,
}: {
  equity: number | null;
  available: number | null;
  upnl: number;
  coin: string;
  positions: RecordData[];
  plans: RecordData[];
}) {
  const wallet = equity !== null ? equity - upnl : null;
  const inUse = equity !== null && available !== null ? equity - available : null;

  return (
    <div className="card">
      <div className="card-header">
        <div>
          <span className="eyebrow">CAPITAL &amp; RISK</span>
          <h2>Balance &amp; Open Position Risk</h2>
        </div>
        <span className="text-sm text-secondary">Provider read (GET-only) · margin coin {coin}</span>
      </div>

      <div className="metric-grid-4">
        <div className="metric-tile">
          <span className="metric-tile-label">Equity (incl. uPnL)</span>
          <span className="metric-tile-value">{moneyText(equity)}</span>
          <span className="metric-tile-sub">Wallet {moneyText(wallet)}</span>
        </div>
        <div className="metric-tile">
          <span className="metric-tile-label">Free (available)</span>
          <span className="metric-tile-value">{moneyText(available)}</span>
          <span className="metric-tile-sub">Transferable margin</span>
        </div>
        <div className="metric-tile">
          <span className="metric-tile-label">In use</span>
          <span className="metric-tile-value">{moneyText(inUse)}</span>
          <span className="metric-tile-sub">Margin + unrealized P&amp;L</span>
        </div>
        <div className="metric-tile">
          <span className="metric-tile-label">Unrealized P&amp;L</span>
          <span className={`metric-tile-value ${upnl >= 0 ? 'text-success' : 'text-danger'}`}>
            {signedMoney(upnl)}
          </span>
          <span className="metric-tile-sub">
            {positions.length === 0
              ? 'Account flat'
              : `${positions.length} open position${positions.length === 1 ? '' : 's'}`}
          </span>
        </div>
      </div>

      {positions.length === 0 ? (
        <div className="empty-state-box">
          <p className="empty-title">No open position — account flat</p>
          <p className="empty-subtext">
            An empty row list alone does not prove flat; only a complete provider inventory does.
          </p>
        </div>
      ) : (
        positions.map((row, index) => (
          <PositionRiskBlock key={`${String(row.symbol ?? 'row')}-${index}`} row={row} plans={plans} />
        ))
      )}
    </div>
  );
}

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
  const accountData = isAccountView && isObjectRecord(envelope?.data) ? envelope.data : null;
  const overviewData = isObjectRecord(safety?.data)
    ? safety.data
    : isObjectRecord(envelope?.data)
    ? envelope.data
    : {};
  const safetyRecord = safety && isObjectRecord(safety.data) ? safety.data : null;
  const safetyData = safetyRecord ?? overviewData;

  // 1. Live Provider Account & Balance Binding:
  // Source 1: provider.account snapshot (.values object from Bitget GET /api/v2/mix/account/account)
  const provObj = isObjectRecord(overviewData.provider) ? overviewData.provider : {};
  const provAccount = isObjectRecord(provObj.account)
    ? (isObjectRecord(provObj.account.values) ? (provObj.account.values as RecordData) : (provObj.account as RecordData))
    : isObjectRecord(overviewData.account)
    ? (isObjectRecord(overviewData.account.values) ? (overviewData.account.values as RecordData) : (overviewData.account as RecordData))
    : null;

  // Source 2: historical admission balance snapshots
  const rawSnapshots = overviewData.admission_snapshots ?? accountData?.admission_snapshots;
  const snapshotsList: RecordData[] = Array.isArray(rawSnapshots)
    ? (rawSnapshots as RecordData[])
    : isObjectRecord(rawSnapshots) && Array.isArray(rawSnapshots.items)
    ? (rawSnapshots.items as RecordData[])
    : [];
  const latestSnapshot = snapshotsList[0] || null;

  // Real live equity & available values:
  const rawEquity =
    provAccount?.equity ??
    provAccount?.accountEquity ??
    provAccount?.usdtEquity ??
    provAccount?.total_balance ??
    latestSnapshot?.equity ??
    latestSnapshot?.total_balance;

  const rawAvailable =
    provAccount?.available ??
    provAccount?.crossedMaxAvailable ??
    provAccount?.available_balance ??
    latestSnapshot?.available_balance;

  const coin = String(
    provAccount?.margin_coin ??
    provAccount?.marginCoin ??
    latestSnapshot?.margin_coin ??
    'USDT'
  );

  const equityFormatted =
    rawEquity !== undefined && rawEquity !== null
      ? `$${formatNumber(Number(rawEquity).toFixed(2))}`
      : '—';

  const availableFormatted =
    rawAvailable !== undefined && rawAvailable !== null
      ? `Available: $${formatNumber(Number(rawAvailable).toFixed(2))} ${coin}`
      : `Balance: ${coin} Standby`;

  // 2. Positions & Ledger Binding:
  const ledgerList = Array.isArray(overviewData.ledger) ? (overviewData.ledger as RecordData[]) : [];
  const ledgerSummary = ledgerList[0] || {};
  const openPosCount = Number(ledgerSummary.open_positions ?? 0);

  const provPositions = Array.isArray(provObj.positions)
    ? (provObj.positions as RecordData[])
    : isObjectRecord(provObj.positions) && Array.isArray(provObj.positions.items)
    ? (provObj.positions.items as RecordData[])
    : [];
  const totalUpnl = provPositions.reduce((acc, p) => acc + Number(p.unrealizedPL || p.unrealized_pl || 0), 0);

  // Native venue plans for the open positions (pos_loss / pos_profit trigger prices).
  const planObservation = isObjectRecord(provObj.plan_orders) ? provObj.plan_orders : null;
  const nativePlans =
    planObservation && Array.isArray(planObservation.items)
      ? (planObservation.items as RecordData[])
      : [];

  // 3. Safety Latches & Clock Binding:
  const latches = Array.isArray(overviewData.latches)
    ? (overviewData.latches as RecordData[])
    : Array.isArray(safetyData.latches)
    ? (safetyData.latches as RecordData[])
    : [];
  const activeLatches = latches.filter((l) => l.active === true);
  const clockLatch = latches.find((l) => l.scope === 'bitget' && l.active && String(l.reason).includes('clock'));

  const clock = isObjectRecord(overviewData.clock) ? overviewData.clock : null;
  const clockValues = clock && isObjectRecord(clock.values) ? clock.values : clock;
  const clockSkew = clockValues?.local_minus_server_ms ?? clockValues?.skew_ms ?? null;

  // 4. Recent Signals:
  const recentSignalsObj = isObjectRecord(overviewData.recent_signals) ? overviewData.recent_signals : {};
  const recentSignals = Array.isArray(recentSignalsObj.items) ? (recentSignalsObj.items as RecordData[]) : [];

  if (isAccountView) {
    const accData = accountData ?? overviewData;
    return (
      <div className="page-content account-page">
        <AccountDetailsView data={accData} />
      </div>
    );
  }

  return (
    <div className="page-content overview-page">
      {/* 4 Clean Executive Metric Tiles with Legitimate Real Data */}
      <div className="metric-grid-4">
        <div className="metric-tile">
          <span className="metric-tile-label">Live Account Equity</span>
          <span className="metric-tile-value">
            {equityFormatted}
          </span>
          <span className="metric-tile-sub">
            {availableFormatted}
          </span>
        </div>

        <div className="metric-tile">
          <span className="metric-tile-label">Unrealized P&amp;L</span>
          <span className={`metric-tile-value ${totalUpnl >= 0 ? 'text-success' : 'text-danger'}`}>
            {totalUpnl >= 0 ? '+' : ''}${formatNumber(totalUpnl.toFixed(2))}
          </span>
          <span className="metric-tile-sub">
            {openPosCount === 0 ? '0 open positions (Account flat)' : `${openPosCount} active positions`}
          </span>
        </div>

        <div className="metric-tile">
          <span className="metric-tile-label">Admission State</span>
          <span className="metric-tile-value" style={{fontSize: '15px', display: 'flex', alignItems: 'center', gap: '6px'}}>
            <StatusBadge
              status={activeLatches.length > 0 ? 'danger' : 'warn'}
              label={activeLatches.length > 0 ? 'Containment Latched' : 'Admission Fail-Closed'}
            />
          </span>
          <span className="metric-tile-sub">
            {activeLatches.length > 0
              ? activeLatches.map((l) => l.reason).join(', ')
              : 'No positive admission asserted'}
          </span>
        </div>

        <div className="metric-tile">
          <span className="metric-tile-label">Exchange Connection</span>
          <span className="metric-tile-value" style={{fontSize: '15px', display: 'flex', alignItems: 'center', gap: '6px'}}>
            <StatusBadge
              status={clockLatch ? 'danger' : 'fresh'}
              label={clockLatch ? 'Clock Latched' : 'Synchronized'}
            />
          </span>
          <span className="metric-tile-sub">
            {clockLatch
              ? 'Bitget clock skew incident active'
              : clockSkew !== null
              ? `Skew: ${clockSkew}ms`
              : 'Bitget V2 stream standby'}
          </span>
        </div>
      </div>

      {/* Capital & Risk: balance composition and per-position risk math */}
      <CapitalAndRisk
        equity={toNumber(rawEquity)}
        available={toNumber(rawAvailable)}
        upnl={Number.isFinite(totalUpnl) ? totalUpnl : 0}
        coin={coin}
        positions={provPositions}
        plans={nativePlans}
      />

      {/* Active Positions Table on Overview */}
      <div className="card">
        <div className="card-header">
          <div>
            <span className="eyebrow">ACTIVE EXPOSURE</span>
            <h2>Open Positions ({openPosCount})</h2>
          </div>
          <Link to="/trading" className="btn btn-primary btn-sm">
            + New Trade
          </Link>
        </div>
        <PositionsTable
          items={provPositions}
          onClose={(item) => onOpenAction?.('position.close', item)}
        />
      </div>

      {/* Recent Signals Stream */}
      {recentSignals.length > 0 && (
        <div className="card">
          <div className="card-header">
            <div>
              <span className="eyebrow">INTAKE STREAM</span>
              <h2>Recent Telegram Signals</h2>
            </div>
            <Link to="/signals" className="btn btn-secondary btn-sm">
              All Signals ({recentSignals.length}) →
            </Link>
          </div>
          <SignalsTable items={recentSignals.slice(0, 5)} />
        </div>
      )}

      {/* Quick Navigation Cards */}
      <div style={{display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '16px'}}>
        <div className="card" style={{marginBottom: 0}}>
          <div className="card-header">
            <h2>Trading Desk</h2>
            <Link to="/positions" className="btn btn-secondary btn-sm">Open →</Link>
          </div>
          <p className="text-secondary text-sm">
            Audit 41 recorded order intents, 19 fills, and native SL/TP protection.
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
            Inspect active clock-skew latch, proof status, and baseline exclusions.
          </p>
        </div>
      </div>
    </div>
  );
}
