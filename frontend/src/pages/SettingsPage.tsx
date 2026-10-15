import React, {useState} from 'react';
import {DomainTable, DomainDetail} from '../domain';
import {SecretStage} from '../forms';
import {Link} from '../nav';
import type {Envelope, Capability, RecordData, Principal} from '../types';
import {isObjectRecord} from '../types';

function TradingDefaultsConfig({
  onSave,
}: {
  onSave: (settings: RecordData) => void;
}) {
  const [mode, setMode] = useState<'PERCENTAGE' | 'FIXED_USDT'>('PERCENTAGE');
  const [allocationPct, setAllocationPct] = useState('0.20');
  const [fixedMarginUsdt, setFixedMarginUsdt] = useState('1.00');
  const [defaultLeverage, setDefaultLeverage] = useState(20);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const settings: RecordData = {
      BITGET_MARGIN_MODE_TYPE: mode,
      BITGET_ALLOCATION_PCT: Number(allocationPct),
      BITGET_MAX_MARGIN_PER_TRADE_USDT: Number(fixedMarginUsdt),
      BITGET_DEFAULT_LEVERAGE: Number(defaultLeverage),
    };
    onSave(settings);
  };

  return (
    <section className="card">
      <div className="card-header">
        <div>
          <span className="eyebrow">AUTOMATED TRADING DEFAULTS</span>
          <h2>Default Margin &amp; Leverage (Signals)</h2>
        </div>
        <span className="badge badge-positive">Live Signal Engine</span>
      </div>
      <p className="text-secondary text-sm" style={{marginBottom: '16px'}}>
        Configure how the automated dispatcher sizes incoming Telegram signals on Bitget futures.
      </p>

      <form onSubmit={handleSubmit}>
        {/* Margin Sizing Mode Selector */}
        <label className="form-group">
          <span className="form-label">Default Margin Sizing Mode</span>
          <div className="side-selector" style={{maxWidth: '480px', marginBottom: '14px'}}>
            <button
              type="button"
              className={`side-btn ${mode === 'PERCENTAGE' ? 'active-long' : ''}`}
              onClick={() => setMode('PERCENTAGE')}
            >
              Percentage of Total USDT (% of Equity)
            </button>
            <button
              type="button"
              className={`side-btn ${mode === 'FIXED_USDT' ? 'active-long' : ''}`}
              onClick={() => setMode('FIXED_USDT')}
            >
              Fixed USDT Quantity ($)
            </button>
          </div>
        </label>

        <div style={{display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: '16px'}}>
          {mode === 'PERCENTAGE' ? (
            <label className="form-group">
              <span className="form-label">
                Margin Allocation: {(Number(allocationPct) * 100).toFixed(0)}% of Equity
              </span>
              <input
                type="range"
                min="0.05"
                max="1.00"
                step="0.05"
                value={allocationPct}
                onChange={(e) => setAllocationPct(e.target.value)}
              />
              <span className="text-muted text-xs">
                Allocates {(Number(allocationPct) * 100).toFixed(0)}% of your available balance per trade.
              </span>
            </label>
          ) : (
            <label className="form-group">
              <span className="form-label">Fixed Margin per Trade (USDT)</span>
              <input
                type="text"
                pattern="^[0-9]+(?:\.[0-9]+)?$"
                required
                value={fixedMarginUsdt}
                onChange={(e) => setFixedMarginUsdt(e.target.value)}
                placeholder="1.00"
              />
              <span className="text-muted text-xs">
                Hard dollar cap in USDT allocated per automated signal.
              </span>
            </label>
          )}

          <label className="form-group">
            <span className="form-label">Default Leverage ({defaultLeverage}x)</span>
            <div style={{display: 'flex', gap: '8px', marginTop: '4px'}}>
              {[5, 10, 15, 20].map((lev) => (
                <button
                  key={lev}
                  type="button"
                  className={`btn btn-sm ${defaultLeverage === lev ? 'btn-primary' : 'btn-secondary'}`}
                  onClick={() => setDefaultLeverage(lev)}
                >
                  {lev}x {lev === 20 ? '(Ceiling)' : ''}
                </button>
              ))}
            </div>
            <span className="text-muted text-xs" style={{marginTop: '4px'}}>
              Ceiling is pinned at 20x for safe liquidation headroom.
            </span>
          </label>
        </div>

        <div style={{marginTop: '16px'}}>
          <button type="submit" className="btn btn-primary btn-sm">
            Save Trading Defaults Draft →
          </button>
        </div>
      </form>
    </section>
  );
}

export function SettingsPage({
  envelope,
  readSection,
  resourceId,
  principal,
  onOpenAction,
  reload,
  render,
}: {
  envelope: Envelope | null;
  readSection: string;
  resourceId: string;
  principal: Principal | null;
  onOpenAction: (action: string, initialArgs?: RecordData, initialTargets?: string[]) => void;
  reload: () => void;
  render: (value: unknown) => React.ReactNode;
}) {
  const data = envelope?.data;
  const items = isObjectRecord(data) && Array.isArray(data.items) ? data.items.filter(isObjectRecord) : [];
  const isOwner = Boolean(principal?.roles?.includes('owner'));

  const getTitle = () => {
    switch (readSection) {
      case 'config':
        return 'Effective Configuration & Gates (UX11)';
      case 'config/history':
        return 'Configuration Revision History (UX11)';
      case 'config/drafts':
        return 'Configuration Drafts (UX11)';
      case 'recovery':
        return 'Safety Latches & Containment Proofs (UX12)';
      case 'recovery/baselines':
        return 'Audited Historical Baselines (UX12)';
      case 'services':
        return 'Container Services & Attestation (UX13)';
      case 'releases':
        return 'Immutable Release Catalog (UX13)';
      case 'backups':
        return 'Encrypted Backups & Restore Readiness (UX14)';
      case 'access':
        return 'Access Control & Security Policy (UX15)';
      case 'access/principals':
        return 'Enrolled Operator Principals (UX15)';
      case 'access/sessions':
        return 'Active Operator Sessions (UX15)';
      case 'access/policy':
        return 'Password Authentication Policy (UX15)';
      default:
        return 'Settings & Operations';
    }
  };

  return (
    <div className="page-content settings-page">
      {/* Configuration Defaults Card */}
      {readSection === 'config' && (
        <>
          <TradingDefaultsConfig
            onSave={(settings) =>
              onOpenAction('config.save_draft', {settings})
            }
          />
          <SecretStage isOwner={isOwner} onChanged={reload} />
        </>
      )}

      {readSection === 'access' && (
        <div className="alert alert-info" data-testid="password-access-policy">
          <strong>Shared-Password Owner Access Model (UX15)</strong>
          <p>
            The console operates under a password-only authentication policy. No username, passkey, OTP or external IDP is required.
            Actions requiring a distinct Tier 3 approver remain honest and unavailable under this shared operator session.
          </p>
        </div>
      )}

      {resourceId ? (
        <DomainDetail section={readSection} data={data} render={render} />
      ) : (
        <section className="card">
          <div className="card-header">
            <div>
              <h2>{getTitle()}</h2>
              <p className="text-secondary">
                Authoritative host environment controls. All destructive mutations require reviewed previews,
                exact confirmation phrases, and immutable audit logs.
              </p>
            </div>
            <div className="header-actions">
              {readSection.startsWith('config') && (
                <>
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    onClick={() => onOpenAction('config.save_draft')}
                  >
                    Create Config Draft
                  </button>
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    onClick={() => onOpenAction('config.apply')}
                  >
                    Apply Config Draft
                  </button>
                </>
              )}
              {readSection.startsWith('recovery') && (
                <>
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    onClick={() => onOpenAction('admission.pause')}
                  >
                    Pause Admission
                  </button>
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    onClick={() => onOpenAction('admission.resume')}
                  >
                    Resume Admission
                  </button>
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    onClick={() => onOpenAction('safety.contain')}
                  >
                    Contain Scope
                  </button>
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    onClick={() => onOpenAction('incident.acknowledge')}
                  >
                    Acknowledge Latch
                  </button>
                </>
              )}
              {readSection.startsWith('services') && (
                <>
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    onClick={() => onOpenAction('maintenance.prepare')}
                  >
                    Prepare Maintenance
                  </button>
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    onClick={() => onOpenAction('service.restart')}
                  >
                    Restart Service
                  </button>
                </>
              )}
              {readSection === 'releases' && (
                <>
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    onClick={() => onOpenAction('release.deploy')}
                  >
                    Deploy Release
                  </button>
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    onClick={() => onOpenAction('release.rollback')}
                  >
                    Rollback Release
                  </button>
                </>
              )}
              {readSection === 'backups' && (
                <>
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    onClick={() => onOpenAction('backup.verify')}
                  >
                    Verify Backup
                  </button>
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    onClick={() => onOpenAction('backup.restore')}
                  >
                    Restore Backup
                  </button>
                </>
              )}
            </div>
          </div>

          <DomainTable section={readSection} items={items} render={render} />
        </section>
      )}
    </div>
  );
}
