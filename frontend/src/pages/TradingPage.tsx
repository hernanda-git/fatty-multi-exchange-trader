import React, {useState} from 'react';
import {PositionsTable, OrdersTable, FillsTable} from '../domain';
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
  const legacyFallbacks = isObjectRecord(data) && Array.isArray(data.unverified_fallback_registrations)
    ? data.unverified_fallback_registrations.filter(isObjectRecord) : [];

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
        <div className="card order-ticket">
          <div className="card-header">
            <div>
              <span className="eyebrow">ORDER TICKET</span>
              <h2>New Manual Trade (UX05)</h2>
            </div>
            <span className="badge badge-warn">Tier 2 Confirmation</span>
          </div>

          <form onSubmit={handleNewTradeSubmit}>
            {/* Long / Short Side Selector */}
            <div className="side-selector">
              <button
                type="button"
                className={`side-btn ${direction === 'LONG' ? 'active-long' : ''}`}
                onClick={() => setDirection('LONG')}
              >
                LONG / BUY
              </button>
              <button
                type="button"
                className={`side-btn ${direction === 'SHORT' ? 'active-short' : ''}`}
                onClick={() => setDirection('SHORT')}
              >
                SHORT / SELL
              </button>
            </div>

            <div style={{display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px'}}>
              <label className="form-group">
                <span className="form-label">Contract Symbol</span>
                <input
                  type="text"
                  required
                  value={symbol}
                  onChange={(e) => setSymbol(e.target.value.toUpperCase())}
                  placeholder="BTCUSDT"
                />
              </label>

              <label className="form-group">
                <span className="form-label">Order Type</span>
                <select
                  value={orderType}
                  onChange={(e) => setOrderType(e.target.value as 'MARKET' | 'LIMIT')}
                >
                  <option value="MARKET">Market Execution</option>
                  <option value="LIMIT">Limit Order</option>
                </select>
              </label>
            </div>

            {orderType === 'LIMIT' && (
              <label className="form-group">
                <span className="form-label">Limit Entry Price ($)</span>
                <input
                  type="text"
                  required
                  pattern="^[0-9]+(?:\.[0-9]+)?$"
                  value={entryPrice}
                  onChange={(e) => setEntryPrice(e.target.value)}
                  placeholder="Limit price"
                />
              </label>
            )}

            <div style={{display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px'}}>
              <label className="form-group">
                <span className="form-label">Stop Loss ($) *</span>
                <input
                  type="text"
                  required
                  pattern="^[0-9]+(?:\.[0-9]+)?$"
                  value={stopLoss}
                  onChange={(e) => setStopLoss(e.target.value)}
                  placeholder="Protective stop price"
                />
              </label>

              <label className="form-group">
                <span className="form-label">Take Profit ($) *</span>
                <input
                  type="text"
                  required
                  pattern="^[0-9]+(?:\.[0-9]+)?$"
                  value={takeProfit}
                  onChange={(e) => setTakeProfit(e.target.value)}
                  placeholder="Profit target price"
                />
              </label>
            </div>

            <div style={{display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px'}}>
              <label className="form-group">
                <span className="form-label">Margin (USDT)</span>
                <input
                  type="text"
                  pattern="^[0-9]+(?:\.[0-9]+)?$"
                  value={margin}
                  onChange={(e) => setMargin(e.target.value)}
                  placeholder="50"
                />
              </label>

              <label className="form-group">
                <span className="form-label">Leverage: {leverage}x</span>
                <input
                  type="range"
                  min="5"
                  max="20"
                  step="1"
                  value={leverage}
                  onChange={(e) => setLeverage(Number(e.target.value))}
                  style={{marginTop: '8px'}}
                />
              </label>
            </div>

            <div style={{marginTop: '20px'}}>
              <button
                type="submit"
                className="btn btn-primary"
                style={{width: '100%', padding: '10px'}}
                data-testid="prepare-trade-btn"
              >
                Review Order Preview →
              </button>
            </div>
          </form>
        </div>
      </div>
    );
  }

  return (
    <div className="page-content trading-page">
      <div className="card">
        <div className="card-header">
          <div>
            <span className="eyebrow">TRADING DESK</span>
            <h2>
              {readSection === 'positions'
                ? 'Active Positions (UX02)'
                : readSection === 'orders'
                ? 'Working Orders & Intents (UX03)'
                : readSection === 'fills'
                ? 'Execution Fills (UX03)'
                : 'SL / TP Protection (UX04)'}
            </h2>
          </div>
          {readSection === 'positions' && (
            <button
              type="button"
              className="btn btn-primary btn-sm"
              onClick={() => onOpenAction('trade.open')}
            >
              + New Position
            </button>
          )}
        </div>
        {readSection === 'protection' && legacyFallbacks.length > 0 && (
          <section role="status" className="alert alert-info" data-testid="legacy-fallback-warning">
            <strong>Historical fallback registrations are not verified protection</strong>
            <p>These records lack an exact environment or provider-position identity.
              They cannot authorize closes and are not counted as protected positions.</p>
            <ul>{legacyFallbacks.map(row => (
              <li key={String(row.id)}>{String(row.symbol)} · {String(row.id)} · ownership unverified</li>
            ))}</ul>
          </section>
        )}

        {readSection === 'positions' && (
          <PositionsTable
            items={items}
            onClose={(item) => onOpenAction('position.close', {}, [String(item.id)])}
          />
        )}

        {readSection === 'orders' && (
          <OrdersTable
            items={items}
            onCancel={(item) => onOpenAction('order.cancel_exact', {client_oid: String(item.client_order_id)}, [String(item.id)])}
          />
        )}

        {readSection === 'fills' && <FillsTable items={items} />}

        {readSection === 'protection' && (
          <PositionsTable
            items={items}
            actionLabel="Review native repair"
            onClose={(item) => onOpenAction('protection.repair_native', {}, [String(item.id)])}
          />
        )}
      </div>
    </div>
  );
}
