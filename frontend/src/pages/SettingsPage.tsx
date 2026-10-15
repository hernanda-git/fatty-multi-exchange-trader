import React, {useState} from 'react';
import {DomainTable, DomainDetail} from '../domain';
import {SecretStage} from '../forms';
import {Link} from '../nav';
import type {Envelope, Capability, RecordData, Principal} from '../types';
import {isObjectRecord} from '../types';

function TradingDefaultsConfig({data, onSave}: {
  data: RecordData;
  onSave: (args: RecordData) => void;
}) {
  const effective = isObjectRecord(data.effective) ? data.effective : null;
  const values = effective && isObjectRecord(effective.values) ? effective.values : null;
  const [changes, setChanges] = useState<Record<string, string>>({});
  const mode = changes.BITGET_MARGIN_MODE_TYPE ?? String(values?.BITGET_MARGIN_MODE_TYPE ?? 'PERCENTAGE');
  const allocation = changes.BITGET_ALLOCATION_PCT ?? String(values?.BITGET_ALLOCATION_PCT ?? '0.20');
  const fixed = changes.BITGET_FIXED_MARGIN_USDT ?? String(values?.BITGET_FIXED_MARGIN_USDT ?? '1');
  const leverage = changes.BITGET_DEFAULT_LEVERAGE ?? String(values?.BITGET_DEFAULT_LEVERAGE ?? '20');
  const baseVersion = data.current_version;
  const ready = Boolean(values && effective?.last_known !== true &&
    typeof baseVersion === 'number' && Number.isInteger(baseVersion) && baseVersion >= 0);
  const change = (key: string, value: string) => setChanges(previous => ({...previous, [key]: value}));
  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!ready) return;
    onSave({
      base_version: baseVersion,
      settings: {
        BITGET_MARGIN_MODE_TYPE: mode,
        BITGET_ALLOCATION_PCT: Number(allocation),
        BITGET_FIXED_MARGIN_USDT: Number(fixed),
        BITGET_DEFAULT_LEVERAGE: Number(leverage),
      },
    });
  }
  return (
    <section className="card" data-testid="trading-defaults">
      <div className="card-header">
        <div><span className="eyebrow">AUTOMATED SIGNALS ONLY</span><h2>Default margin &amp; leverage</h2></div>
      </div>
      <p className="text-secondary text-sm">
        Applies to new Telegram signal trades, not the manual order ticket or existing positions.
        Percentage sizing uses total USDT equity; available funds and the 1 USDT per-trade safety cap still limit execution.
        Leverage may decrease when the signal's stop requires more liquidation headroom.
      </p>
      {!ready && <p role="status">Waiting for effective settings and configuration version. Saving is unavailable.</p>}
      <form onSubmit={submit}>
        <fieldset disabled={!ready}>
          <label className="form-group">
            <span className="form-label">Margin sizing mode</span>
            <select value={mode} onChange={event => change('BITGET_MARGIN_MODE_TYPE', event.target.value)}>
              <option value="PERCENTAGE">Percentage of total USDT equity</option>
              <option value="FIXED_USDT">Fixed USDT amount</option>
            </select>
          </label>
          {mode === 'PERCENTAGE' ? (
            <label className="form-group">
              <span className="form-label">Equity allocation (%)</span>
              <input type="number" required min="0.01" max="100" step="0.01"
                value={Number(allocation) * 100}
                onChange={event => change('BITGET_ALLOCATION_PCT', String(Number(event.target.value) / 100))} />
            </label>
          ) : (
            <label className="form-group">
              <span className="form-label">Margin per trade (USDT)</span>
              <input type="number" required min="0.01" max="1" step="0.01" value={fixed}
                onChange={event => change('BITGET_FIXED_MARGIN_USDT', event.target.value)} />
            </label>
          )}
          <label className="form-group">
            <span className="form-label">Default leverage (x)</span>
            <input type="number" required min="5" max="20" step="1" value={leverage}
              onChange={event => change('BITGET_DEFAULT_LEVERAGE', event.target.value)} />
          </label>
          <p className="text-muted text-xs">
            Initial values reflect the worker's effective environment (legacy defaults: 20% and 20x).
            Saving creates a draft only. Review and apply the draft separately before it changes runtime sizing.
          </p>
          <button type="submit" className="btn btn-primary btn-sm">Review trading defaults draft</button>
        </fieldset>
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
      {readSection === 'config' && !resourceId && (
        <>
          <TradingDefaultsConfig
            data={isObjectRecord(data) ? data : {}}
            onSave={args => onOpenAction('config.save_draft', args)}
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
