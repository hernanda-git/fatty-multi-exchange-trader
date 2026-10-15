import React, {useState} from 'react';
import {Link} from '../nav';
import {Value, StatusBadge, formatNumber} from '../primitives';
import {DomainTable, DomainDetail} from '../domain';
import {type Envelope, type Capability, type RecordData, isObjectRecord} from '../types';

export function TradingPage({
  envelope,
  readSection,
  resourceId,
  currentScreen,
  capabilities,
  onOpenAction,
  render,
}: {
  envelope: Envelope | null;
  readSection: string;
  resourceId: string;
  currentScreen: string;
  capabilities: Capability[];
  onOpenAction: (action: string, initialArgs?: RecordData, initialTargets?: string[]) => void;
  render: (value: unknown) => React.ReactNode;
}) {
  const data = envelope ? envelope.data : null;
  const items = isObjectRecord(data) && Array.isArray(data.items) ? data.items.filter(isObjectRecord) : [];

  // Manual New Trade form state (UX05)
  const [symbol, setSymbol] = useState('BTCUSDT');
  const [direction, setDirection] = useState<'LONG' | 'SHORT'>('LONG');
  const [orderType, setOrderType] = useState<'MARKET' | 'LIMIT'>('MARKET');
  const [entryPrice, setEntryPrice] = useState('');
  const [stopLoss, setStopLoss] = useState('');
  const [takeProfit, setTakeProfit] = useState('');
  const [margin, setMargin] = useState('50');
  const [leverage, setLeverage] = useState(10);

  const isNewTrade = currentScreen === 'trading';

  if (isNewTrade) {
    const handleNewTradeSubmit = (e: React.FormEvent) => {
      e.preventDefault();
      const payload: RecordData = {
        symbol: symbol.toUpperCase().trim(),
        direction,
        order_type: orderType,
        stop_loss: stopLoss.trim(),
        take_profit: takeProfit.trim(),
        margin: margin.trim(),
        leverage: Number(leverage),
      };
      if (orderType === 'LIMIT' && entryPrice.trim()) {
        payload.entry_price = entryPrice.trim();
      }
      onOpenAction('trade.open', payload);
    };

    return (
      <div className="page-content new-trade-page">
        <section className="card form-hero-card">
          <div className="card-header">
            <div>
              <span className="eyebrow">CONTROLLED OPERATOR ENTRY</span>
              <h2>New Manual Trade (UX05)</h2>
            </div>
            <span className="badge badge-neutral">Tier 2 · Confirmation Required</span>
          </div>
          <p className="text-secondary">
            Submitting this form prepares an authoritative read-only preview. All risk caps, independent safety
            latches, and server policies are validated before enqueueing. Accepted is not executed.
          </p>

          <form className="trade-entry-form" onSubmit={handleNewTradeSubmit}>
            <div className="form-grid">
              <label className="form-group">
                <span className="form-label">Contract Symbol *</span>
                <input
                  type="text"
                  required
                  pattern="^[A-Z0-9]+$"
                  value={symbol}
                  onChange={(e) => setSymbol(e.target.value.toUpperCase())}
                  placeholder="e.g. BTCUSDT"
                />
              </label>

              <label className="form-group">
                <span className="form-label">Direction *</span>
                <select value={direction} onChange={(e) => setDirection(e.target.value as 'LONG' | 'SHORT')}>
                  <option value="LONG">LONG (Buy)</option>
                  <option value="SHORT">SHORT (Sell)</option>
                </select>
              </label>

              <label className="form-group">
                <span className="form-label">Order Type *</span>
                <select value={orderType} onChange={(e) => setOrderType(e.target.value as 'MARKET' | 'LIMIT')}>
                  <option value="MARKET">MARKET</option>
                  <option value="LIMIT">LIMIT</option>
                </select>
              </label>

              {orderType === 'LIMIT' && (
                <label className="form-group">
                  <span className="form-label">Entry Price *</span>
                  <input
                    type="text"
                    required
                    pattern="^[0-9]+(?:\.[0-9]+)?$"
                    value={entryPrice}
                    onChange={(e) => setEntryPrice(e.target.value)}
                    placeholder="Limit entry price"
                  />
                </label>
              )}

              <label className="form-group">
                <span className="form-label">Stop Loss Price *</span>
                <input
                  type="text"
                  required
                  pattern="^[0-9]+(?:\.[0-9]+)?$"
                  value={stopLoss}
                  onChange={(e) => setStopLoss(e.target.value)}
                  placeholder="Mandatory protective stop loss"
                />
              </label>

              <label className="form-group">
                <span className="form-label">Take Profit Price *</span>
                <input
                  type="text"
                  required
                  pattern="^[0-9]+(?:\.[0-9]+)?$"
                  value={takeProfit}
                  onChange={(e) => setTakeProfit(e.target.value)}
                  placeholder="Mandatory take profit price"
                />
              </label>

              <label className="form-group">
                <span className="form-label">Margin (USDT)</span>
                <input
                  type="text"
                  pattern="^[0-9]+(?:\.[0-9]+)?$"
                  value={margin}
                  onChange={(e) => setMargin(e.target.value)}
                  placeholder="e.g. 50"
                />
              </label>

              <label className="form-group">
                <span className="form-label">Leverage ({leverage}x)</span>
                <input
                  type="range"
                  min="5"
                  max="20"
                  step="1"
                  value={leverage}
                  onChange={(e) => setLeverage(Number(e.target.value))}
                />
              </label>
            </div>

            <div className="form-actions">
              <button type="submit" className="btn btn-primary" data-testid="prepare-trade-btn">
                Review Trade Preview →
              </button>
            </div>
          </form>
        </section>
      </div>
    );
  }

  return (
    <div className="page-content trading-page">
      {resourceId ? (
        <DomainDetail section={readSection} data={data} render={render} />
      ) : (
        <section className="card">
          <div className="card-header">
            <div>
              <h2>{readSection === 'positions' ? 'Active Positions (UX02)' : readSection === 'orders' ? 'Orders & Intents (UX03)' : readSection === 'fills' ? 'Fills & Ledger (UX03)' : 'Protection & Fallbacks (UX04)'}</h2>
            </div>
            <div className="header-actions">
              {readSection === 'positions' && (
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={() => onOpenAction('position.close')}
                >
                  Close Position
                </button>
              )}
              {readSection === 'orders' && (
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={() => onOpenAction('order.cancel_exact')}
                >
                  Cancel Exact Order
                </button>
              )}
              {readSection === 'protection' && (
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={() => onOpenAction('protection.repair_native')}
                >
                  Repair Native Protection
                </button>
              )}
            </div>
          </div>

          <DomainTable section={readSection} items={items} render={render} />
        </section>
      )}
    </div>
  );
}
