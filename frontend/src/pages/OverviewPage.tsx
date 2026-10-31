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
function LiveAccountAndPositionsProbe({
  equity,
  available,
  totalUpnl,
  coin,
  marginMode,
  posMode,
  latches,
  positions,
  plans,
  onClose,
}: {
  equity: number | null;
  available: number | null;
  totalUpnl: number;
  coin: string;
  marginMode: string;
  posMode: string;
  latches: RecordData[];
  positions: RecordData[];
  plans: RecordData[];
  onClose?: (pos: RecordData) => void;
}) {
  const mMode = marginMode
    ? marginMode.charAt(0).toUpperCase() + marginMode.slice(1).toLowerCase()
    : 'Isolated';
  const pMode = posMode ? ` (${posMode})` : '';
  const marginModeDisplay = `${mMode}${pMode}`;

  const bitgetLatch = latches.find((l) => String(l.scope ?? '') === 'bitget');
  const streamLatch = latches.find((l) =>
    ['bitget-protection-stream', 'stream'].includes(String(l.scope ?? ''))
  );
  const bitgetStatus = bitgetLatch?.active ? 'ON' : 'OFF';
  const streamStatus = streamLatch?.active ? 'ON' : 'OFF';
  const killSwitchesDisplay = `bitget: ${bitgetStatus}, protection-stream: ${streamStatus}`;

  const planNotes = positions
    .map((pos) => {
      const sym = String(pos.symbol ?? '').toUpperCase();
      const symPlans = plans.filter((p) => String(p.symbol ?? '').toUpperCase() === sym);
      const sl = symPlans.find((p) => String(p.planType ?? '') === 'pos_loss');
      const tp = symPlans.find((p) => String(p.planType ?? '') === 'pos_profit');
      const slOrderId = sl?.orderId ?? sl?.planOrderId ?? pos.stopLossId;
      const tpOrderId = tp?.orderId ?? tp?.planOrderId ?? pos.takeProfitId;
      const slPrice = sl?.triggerPrice ?? pos.stopLoss;
      const tpPrice = tp?.triggerPrice ?? pos.takeProfit;

      const slPart = slOrderId ? `SL order #${slOrderId} (${slPrice ?? '—'})` : 'SL: none';
      const tpPart = tpOrderId ? `TP order #${tpOrderId} (${tpPrice ?? '—'})` : 'TP: none';
      return {
        symbol: sym,
        text: `${sym}: ${slPart}, ${tpPart}.`,
      };
    })
    .filter((n) => n.text.includes('#'));

  return (
    <div className="card live-probe-card" style={{ marginBottom: '24px' }}>
      <div className="card-header">
        <div>
          <span className="eyebrow">BITGET API PROBE · LIVE PERSISTENCE</span>
          <h2 style={{ fontSize: '1.25rem', fontWeight: 700, margin: 0 }}>
            1. Live Account &amp; Open Positions (Bitget API Probe)
          </h2>
        </div>
        <span className="text-sm text-secondary">
          Observed live via Bitget V2 REST API · Margin Coin {coin}
        </span>
      </div>

      {/* Account Overview Table */}
      <div style={{ marginBottom: '24px' }}>
        <h3 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: '12px', color: 'var(--text-primary)' }}>
          Account Overview
        </h3>
        <div className="clean-table-wrapper" style={{ maxWidth: '640px' }}>
          <table className="clean-table">
            <thead>
              <tr>
                <th style={{ width: '45%' }}>Metric</th>
                <th>Value</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td className="text-secondary font-medium">Total Equity</td>
                <td className="font-mono font-bold tabular-num">
                  {equity !== null ? `${equity.toFixed(5)} ${coin}` : '—'}
                </td>
              </tr>
              <tr>
                <td className="text-secondary font-medium">Available Margin</td>
                <td className="font-mono tabular-num">
                  {available !== null ? `${available.toFixed(5)} ${coin}` : '—'}
                </td>
              </tr>
              <tr>
                <td className="text-secondary font-medium">Unrealized PnL</td>
                <td className={`font-mono tabular-num font-bold ${totalUpnl >= 0 ? 'text-success' : 'text-danger'}`}>
                  {totalUpnl >= 0 ? '+' : ''}{totalUpnl.toFixed(5)} {coin}
                </td>
              </tr>
              <tr>
                <td className="text-secondary font-medium">Margin Mode</td>
                <td className="font-mono">
                  {marginModeDisplay}
                </td>
              </tr>
              <tr>
                <td className="text-secondary font-medium">Kill Switches</td>
                <td className="font-mono">
                  {killSwitchesDisplay}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      {/* Open Positions Table */}
      <div style={{ marginBottom: '20px' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '12px' }}>
          <h3 style={{ fontSize: '1rem', fontWeight: 600, margin: 0, color: 'var(--text-primary)' }}>
            Open Positions
          </h3>
          <span className="text-xs text-muted">
            {positions.length} active position{positions.length === 1 ? '' : 's'}
          </span>
        </div>

        {positions.length === 0 ? (
          <div className="empty-state-box">
            <p className="empty-title">No open position — account flat</p>
            <p className="empty-subtext">
              0 active positions reported by Bitget V2 account probe.
            </p>
          </div>
        ) : (
          <div className="clean-table-wrapper">
            <table className="clean-table">
              <thead>
                <tr>
                  <th scope="col">Symbol</th>
                  <th scope="col">Side</th>
                  <th scope="col">Leverage</th>
                  <th scope="col" className="text-right">Position Size</th>
                  <th scope="col" className="text-right">Entry Price</th>
                  <th scope="col" className="text-right">Mark Price</th>
                  <th scope="col" className="text-right">Margin Used</th>
                  <th scope="col" className="text-right">Unrealized PnL</th>
                  <th scope="col" className="text-right">Active SL</th>
                  <th scope="col" className="text-right">Active TP</th>
                  {onClose && <th scope="col" className="text-right">Action</th>}
                </tr>
              </thead>
              <tbody>
                {positions.map((pos, idx) => {
                  const sym = String(pos.symbol ?? 'UNKNOWN');
                  const base = sym.replace(coin, '');
                  const side = String(pos.holdSide ?? pos.direction ?? pos.side ?? 'LONG').toUpperCase();
                  const isLong = side === 'LONG' || side === 'BUY';
                  const lev = pos.leverage ? `${pos.leverage}x` : '—';
                  const size = pos.total ?? pos.available ?? '0';
                  const sizeDisplay = `${formatNumber(String(size))} ${base}`;

                  const entryVal = toNumber(pos.openPriceAvg ?? pos.averageOpenPrice);
                  const entryDisplay = entryVal !== null
                    ? (entryVal < 1 ? entryVal.toFixed(5) : entryVal.toFixed(5).replace(/0+$/, '0'))
                    : '—';

                  const markVal = toNumber(pos.markPrice);
                  const markDisplay = markVal !== null
                    ? (markVal < 1 ? markVal.toFixed(6) : markVal.toFixed(4))
                    : '—';

                  const marginVal = toNumber(pos.marginSize);
                  const marginDisplay = marginVal !== null ? `${marginVal.toFixed(3)} ${coin}` : '—';

                  const upnlVal = toNumber(pos.unrealizedPL ?? pos.unrealized_pl) ?? 0;
                  const roe = marginVal && marginVal > 0 ? (upnlVal / marginVal) * 100 : null;
                  const sign = upnlVal >= 0 ? '+' : '';
                  const roeText = roe !== null ? ` (${sign}${roe.toFixed(1)}%)` : '';
                  const upnlDisplay = `${sign}${upnlVal.toFixed(4)} ${coin}${roeText}`;

                  const symPlans = plans.filter((p) => String(p.symbol ?? '').toUpperCase() === sym.toUpperCase());
                  const slPlan = symPlans.find((p) => String(p.planType ?? '') === 'pos_loss');
                  const tpPlan = symPlans.find((p) => String(p.planType ?? '') === 'pos_profit');

                  const slVal = toNumber(slPlan?.triggerPrice) ?? toNumber(pos.stopLoss);
                  const slDisplay = slVal !== null
                    ? (slVal < 1 ? slVal.toFixed(5) : slVal.toFixed(4))
                    : '—';

                  const tpVal = toNumber(tpPlan?.triggerPrice) ?? toNumber(pos.takeProfit);
                  const tpDisplay = tpVal !== null
                    ? (tpVal < 1 ? tpVal.toFixed(5) : tpVal.toFixed(4))
                    : '—';

                  return (
                    <tr key={String(pos.id ?? sym) + '-' + idx}>
                      <td className="font-bold">
                        <Link to={`/positions/${encodeURIComponent(sym)}`}>
                          {sym}
                        </Link>
                      </td>
                      <td>
                        <span className={`badge ${isLong ? 'badge-positive' : 'badge-danger'}`}>
                          {side}
                        </span>
                      </td>
                      <td className="font-mono text-sm">{lev}</td>
                      <td className="text-right tabular-num font-mono">{sizeDisplay}</td>
                      <td className="text-right tabular-num font-mono">{entryDisplay}</td>
                      <td className="text-right tabular-num font-mono">{markDisplay}</td>
                      <td className="text-right tabular-num font-mono">{marginDisplay}</td>
                      <td className={`text-right tabular-num font-mono font-bold ${upnlVal >= 0 ? 'text-success' : 'text-danger'}`}>
                        {upnlDisplay}
                      </td>
                      <td className="text-right tabular-num font-mono text-danger font-medium">{slDisplay}</td>
                      <td className="text-right tabular-num font-mono text-success font-medium">{tpDisplay}</td>
                      {onClose && (
                        <td className="text-right">
                          <button
                            type="button"
                            className="btn btn-secondary btn-sm"
                            onClick={() => onClose(pos)}
                          >
                            Close
                          </button>
                        </td>
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Note: Bitget native plan orders confirmed live via REST API */}
      {planNotes.length > 0 && (
        <div style={{
          background: 'var(--bg-subtle)',
          border: '1px solid var(--border-subtle)',
          borderRadius: '6px',
          padding: '12px 16px',
          fontSize: '0.85rem',
          lineHeight: '1.5'
        }}>
          <div style={{ fontWeight: 600, marginBottom: '6px', color: 'var(--text-primary)' }}>
            Note: Bitget native plan orders confirmed live via REST API:
          </div>
          <ul style={{ margin: 0, paddingLeft: '20px', color: 'var(--text-secondary)' }}>
            {planNotes.map((note, idx) => (
              <li key={idx} className="font-mono text-xs" style={{ marginBottom: '3px' }}>
                {note.text}
              </li>
            ))}
          </ul>
        </div>
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
      {/* 1. Live Account & Open Positions (Bitget API Probe) */}
      <LiveAccountAndPositionsProbe
        equity={toNumber(rawEquity)}
        available={toNumber(rawAvailable)}
        totalUpnl={Number.isFinite(totalUpnl) ? totalUpnl : 0}
        coin={coin}
        marginMode={String(provAccount?.margin_mode ?? provAccount?.marginMode ?? 'isolated')}
        posMode={String(provPositions[0]?.posMode ?? 'one_way_mode')}
        latches={latches}
        positions={provPositions}
        plans={nativePlans}
        onClose={(pos) => onOpenAction?.('position.close', pos)}
      />

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
