import React, {useEffect, useRef, useState} from 'react';
import {api, ApiError, authorize} from '../api';
import {DomainTable, DomainDetail} from '../domain';
import {SecretStage} from '../forms';
import {PushNotificationSettings} from './PushNotifications';
import {ErrorBox, Value} from '../primitives';
import {Link} from '../nav';
import type {Envelope, Capability, RecordData, Principal} from '../types';
import {isObjectRecord} from '../types';

const CONFIG_INPUTS = [
  {key: 'BITGET_ALLOCATION_PCT', label: 'Equity allocation (%)', help: 'Percentage of total USDT equity used for a new signal trade. The 1 USDT safety cap still applies.', min: 1, max: 100, step: 'any'},
  {key: 'BITGET_FIXED_MARGIN_USDT', label: 'Margin per trade (USDT)', help: 'Fixed margin for a new signal trade, limited to 1 USDT.', min: 0, max: 1, step: 'any'},
  {key: 'BITGET_DEFAULT_LEVERAGE', label: 'Default leverage (×)', help: 'Applies to new signal trades; stop-loss liquidation headroom can reduce leverage.', min: 5, max: 20, step: '1'},
  {key: 'BITGET_DISPATCH_POLL_SECONDS', label: 'Signal dispatch interval (seconds)', help: 'How often the dispatcher looks for a new trade signal.', min: 1, max: 300, step: '1'},
  {key: 'BITGET_MONITOR_POLL_SECONDS', label: 'Position monitoring interval (seconds)', help: 'How often the monitor checks positions and protection.', min: 1, max: 30, step: '1'},
  {key: 'NOTIFICATION_RETRY_SECONDS', label: 'Notification retry delay (seconds)', help: 'Wait between attempts to deliver a failed notification.', min: 10, max: 300, step: '1'},
  {key: 'NOTIFICATION_MAX_ATTEMPTS', label: 'Maximum notification attempts', help: 'Maximum delivery attempts for each notification.', min: 1, max: 20, step: '1'},
  {key: 'ANALYZER_CHANNEL_IDS', label: 'Live analysis channel IDs', help: 'Telegram numeric channel IDs allowed for live analysis, separated by commas. These must also be intake sources.'},
  {key: 'TELEGRAM_SOURCE_CHANNELS', label: 'Telegram intake channels', help: 'Negative numeric channel IDs or @usernames, separated by commas (for example, -1001234567890, @signals).'},
];
type SaveOperation = {key: string; payload: RecordData; jobId?: string};
const SAVE_REASON = 'Save runtime configuration from the prefilled settings form';

function TradingDefaultsConfig({data, capability, scope, principal, reload}: {
  data: RecordData;
  capability?: Capability;
  scope: RecordData;
  principal: Principal | null;
  reload: () => void;
}) {
  const effective = isObjectRecord(data.effective) ? data.effective : null;
  const workerValues = effective?.values;
  const values: RecordData = effective?.last_known !== true && isObjectRecord(workerValues) ? {...workerValues} : {};
  const consumers = isObjectRecord(data.consumers) ? data.consumers : null;
  const conflicted: Record<string, true> = {};
  const consumerValues: RecordData = {};
  if (consumers?.last_known !== true && Array.isArray(consumers?.items)) {
    for (const service of consumers.items) {
      if (!isObjectRecord(service) || service.last_known === true || !service.runtime_observed_at || !isObjectRecord(service.config_fields)) continue;
      for (const [key, value] of Object.entries(service.config_fields)) {
        if (value === null || value === undefined) continue;
        if (key in consumerValues && JSON.stringify(consumerValues[key]) !== JSON.stringify(value)) conflicted[key] = true;
        consumerValues[key] = value;
      }
    }
  }
  Object.assign(values, consumerValues);
  for (const key of Object.keys(conflicted)) delete values[key];
  const storageKey = `console.config.save.${principal?.id}.${String(scope.account_id)}.${String(scope.environment)}`;
  const [changes, setChanges] = useState<Record<string, string>>({});
  const [editVersion, setEditVersion] = useState<number | null>(null);
  const [preview, setPreview] = useState<RecordData | null>(null);
  const [phrase, setPhrase] = useState('');
  const [reference, setReference] = useState('');
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [status, setStatus] = useState('');
  const [lastJobId, setLastJobId] = useState('');
  const [monitor, setMonitor] = useState(0);
  const [operation, setOperation] = useState<SaveOperation | null>(() => {
    try {
      const saved: unknown = JSON.parse(sessionStorage.getItem(storageKey) || 'null');
      return isObjectRecord(saved) && typeof saved.key === 'string' && isObjectRecord(saved.payload)
        ? {key: saved.key, payload: saved.payload, jobId: typeof saved.jobId === 'string' ? saved.jobId : undefined} : null;
    } catch { return null; }
  });
  const submitting = useRef(false);
  const reloadRef = useRef(reload);
  reloadRef.current = reload;
  function remember(next: SaveOperation | null) {
    if (next) sessionStorage.setItem(storageKey, JSON.stringify(next));
    else sessionStorage.removeItem(storageKey);
    setOperation(next);
  }
  useEffect(() => {
    if (!operation?.jobId) return;
    const controller = new AbortController();
    let timer: number | undefined;
    async function poll() {
      try {
        const response = await api<Envelope>(`/jobs/${encodeURIComponent(operation!.jobId!)}`, undefined, {}, controller.signal);
        if (controller.signal.aborted) return;
        const job = isObjectRecord(response.data) ? response.data : {};
        setLastJobId(operation!.jobId!);
        const state = String(job.state || 'unknown');
        if (['succeeded', 'failed', 'partially_succeeded', 'rejected', 'expired', 'cancelled'].includes(state)) {
          const result = isObjectRecord(job.result) ? job.result : {};
          if (state === 'succeeded' && result.effective === true) {
            setStatus('Saved and effective. The running services confirmed the new configuration.');
            setChanges({});
            setEditVersion(null);
          } else {
            setStatus('');
            setError(new Error(`Configuration was not confirmed effective (job ${state}). Review the job details before saving again.`));
          }
          remember(null);
          setBusy(false);
          reloadRef.current();
          return;
        }
        setStatus(state === 'reconciliation_pending'
          ? 'Save needs reconciliation: runtime effect is not yet confirmed. Do not submit another save.'
          : `Saving configuration: ${state.replace(/_/g, ' ')}. Waiting for running-service confirmation…`);
        timer = window.setTimeout(poll, 2000);
      } catch (e) {
        if (controller.signal.aborted) return;
        setError(e);
        setStatus('Could not check the save status. The durable job may still be running; no new save will be submitted.');
        setBusy(false);
      }
    }
    setBusy(true);
    void poll();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [operation?.jobId, monitor]);

  const currentVersion = data.current_version;
  const ready = typeof currentVersion === 'number' && Number.isInteger(currentVersion) && currentVersion >= 0;
  const owner = Boolean(principal?.roles?.includes('owner'));
  const allowed = owner && capability?.preview_allowed === true;
  const text = (key: string) => changes[key] ?? (Array.isArray(values[key]) ? values[key].join(', ') : String(values[key] ?? ''));
  const mode = text('BITGET_MARGIN_MODE_TYPE');
  const tier = Number(preview?.danger_tier ?? capability?.danger_tier ?? 0);
  const confirmed = Boolean(preview && acknowledged && Array.isArray(preview.gates) && preview.gates.every(gate => isObjectRecord(gate) && gate.verdict === 'pass') && (tier < 2 || (phrase === preview.confirmation_phrase && reference.trim())));
  function change(key: string, value: string) {
    if (editVersion === null && typeof currentVersion === 'number') setEditVersion(currentVersion);
    setChanges(previous => ({...previous, [key]: value}));
    setPreview(null);
    setPhrase('');
    setAcknowledged(false);
    setStatus('');
    setError(null);
  }
  function settings(): RecordData {
    const result: RecordData = {};
    if ('BITGET_MARGIN_MODE_TYPE' in changes) {
      if (!['PERCENTAGE', 'FIXED_USDT'].includes(mode)) throw new Error('Choose a margin sizing mode.');
      result.BITGET_MARGIN_MODE_TYPE = mode;
    }
    for (const field of CONFIG_INPUTS) {
      if (!(field.key in changes)) continue;
      const raw = text(field.key).trim();
      if (field.key.endsWith('CHANNEL_IDS') || field.key === 'TELEGRAM_SOURCE_CHANNELS') {
        const entries = raw.split(/[\s,]+/).filter(Boolean);
        if (entries.length < 1 || entries.length > 100 || new Set(entries).size !== entries.length) throw new Error(`${field.label}: enter 1–100 unique channels.`);
        result[field.key] = entries.map(value => {
          if (field.key === 'TELEGRAM_SOURCE_CHANNELS' && /^@[A-Za-z][A-Za-z0-9_]{4,31}$/.test(value)) return value;
          const number = Number(value);
          if (!/^-?\d+$/.test(value) || !Number.isSafeInteger(number) || (field.key === 'TELEGRAM_SOURCE_CHANNELS' && number >= 0)) throw new Error(`${field.label}: invalid channel ${value}.`);
          return number;
        });
      } else {
        const number = field.key === 'BITGET_ALLOCATION_PCT' ? Number(raw) * 100 : Number(raw);
        if (!raw || !Number.isFinite(number) || number < field.min! || number > field.max! || (field.step === '1' && !Number.isInteger(number)) || (field.key === 'BITGET_FIXED_MARGIN_USDT' && number <= 0)) throw new Error(`${field.label}: enter a valid value within the displayed limits.`);
        result[field.key] = field.key === 'BITGET_ALLOCATION_PCT' ? number / 100 : number;
      }
    }
    if (Object.keys(result).length === 0) throw new Error('Change a setting before saving.');
    return result;
  }
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (submitting.current || operation?.jobId || !ready || !allowed) return;
    submitting.current = true;
    setBusy(true);
    setError(null);
    let pending = operation;
    try {
      if (pending) {
        setStatus('Recovering the existing save request…');
        try {
          const receipt = await api<Envelope>(`/jobs/by-idempotency/${encodeURIComponent(pending.key)}?action=config.save`);
          if (!isObjectRecord(receipt.data) || typeof receipt.data.id !== 'string') throw new Error('The save receipt was unreadable. No new job was submitted.');
          remember({...pending, jobId: receipt.data.id});
          setPreview(null);
          return;
        } catch (receiptError) {
          if (!(receiptError instanceof ApiError) || receiptError.status !== 404) throw receiptError;
        }
      }
      if (!pending) {
        let reviewed = preview;
        if (!reviewed) {
          setStatus('Checking permissions, configuration version and safety gates…');
          reviewed = await api<RecordData>('/previews', {action: 'config.save', arguments: {base_version: editVersion ?? currentVersion, settings: settings()}, target_refs: [], reason: SAVE_REASON, scope, policy_version: capability?.policy_version});
          setPreview(reviewed);
          if (!Array.isArray(reviewed.gates) || reviewed.gates.some(gate => !isObjectRecord(gate) || gate.verdict !== 'pass')) throw new Error('A required safety gate did not pass. Resolve the gates below, then edit a setting to request a fresh review.');
          setStatus('Review the runtime changes and complete the required security confirmation below, then press Save.');
          return;
        }
        if (!confirmed) throw new Error('Complete the required security confirmation before saving.');
        if (Date.parse(String(reviewed.expires_at)) <= Date.now()) { setPreview(null); throw new Error('Security preview expired. Press Save to review a fresh preview.'); }
        if (Number(reviewed.danger_tier) >= 3) throw new Error('This action requires a distinct approver and cannot be saved from this form.');
        setStatus('Authorizing these exact configuration changes…');
        await authorize({preview_digest: String(reviewed.preview_digest)});
        pending = {key: crypto.randomUUID(), payload: {preview_id: reviewed.id, preview_digest: reviewed.preview_digest, confirmation_phrase: phrase || null, reason: SAVE_REASON, approval_reference: reference.trim() || null, policy_version: reviewed.policy_version, resource_version: reviewed.resource_version}};
        remember(pending);
      }
      setStatus('Submitting the authorized save…');
      const job = await api<RecordData>('/jobs', pending.payload, {'Idempotency-Key': pending.key, 'If-Match': String(pending.payload.resource_version)});
      if (typeof job.id !== 'string') throw new Error('Save response did not include a job ID. Retry to recover the same request, not create another job.');
      remember({...pending, jobId: job.id});
      setPreview(null);
      setStatus('Save queued. Waiting for running-service confirmation…');
    } catch (e) {
      setError(e);
      if (pending) {
        try {
          const receipt = await api<Envelope>(`/jobs/by-idempotency/${encodeURIComponent(pending.key)}?action=config.save`);
          if (!isObjectRecord(receipt.data) || typeof receipt.data.id !== 'string') throw new Error('The save receipt was unreadable.');
          remember({...pending, jobId: receipt.data.id});
          setPreview(null);
          setError(null);
          setStatus('Recovered the existing save. Waiting for runtime confirmation…');
        } catch (receiptError) {
          const rejectedCodes = ['preview_expired', 'policy_changed', 'resource_changed', 'gate_closed', 'confirmation_mismatch', 'approval_reference_required'];
          if (receiptError instanceof ApiError && receiptError.status === 404 && e instanceof ApiError && rejectedCodes.includes(String(e.detail.code))) {
            remember(null);
            setPreview(null);
            setStatus('The server rejected this request and no job receipt exists. Your edits are preserved. Refresh and review them before saving again.');
          } else {
            setStatus('The save outcome is unknown. Retry Save checks the receipt and uses the same durable request; no duplicate job will be created.');
          }
        }
      } else {
        setStatus('Configuration has not been confirmed saved. Resolve the error before saving again.');
      }
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }
  return (
    <section className="card" data-testid="trading-defaults">
      <div className="card-header"><div><span className="eyebrow">RUNTIME CONFIGURATION</span><h2>Trading &amp; signal settings</h2></div></div>
      <p className="text-secondary text-sm">Current effective values are filled in below. Save applies these settings to the running services and may restart affected workers. Trading defaults apply to new Telegram signal trades, not manual orders or existing positions. Safety limits remain enforced.</p>
      {!ready && <p role="status">Waiting for the current configuration version. Saving is unavailable.</p>}
      <p className="text-muted text-xs">Only edited settings are saved. “Not observed” means no current runtime value is available; leaving it unchanged preserves the existing setting.</p>
      {!allowed && <p role="status">Saving requires owner permission and an available configuration save capability.</p>}
      {error != null && <ErrorBox error={error} />}
      {status && <p role="status" aria-live="polite">{status}</p>}
      {(operation?.jobId || lastJobId) && <p><Link to={`/audit/jobs/${encodeURIComponent(operation?.jobId || lastJobId)}`}>View save job</Link></p>}
      {operation?.jobId && !busy && <button type="button" className="btn btn-secondary btn-sm" onClick={() => {setError(null); setMonitor(value => value + 1);}}>Check save status</button>}
      <form onSubmit={submit}>
        <fieldset disabled={!ready || !allowed || busy || Boolean(operation)}>
          <label className="form-group"><span className="form-label">Margin sizing mode</span><select value={mode} onChange={event => change('BITGET_MARGIN_MODE_TYPE', event.target.value)}><option value="" disabled>Not observed — choose to change</option><option value="PERCENTAGE">Percentage of total USDT equity</option><option value="FIXED_USDT">Fixed USDT amount</option></select></label>
          {CONFIG_INPUTS.map(field => {
            const hidden = (field.key === 'BITGET_ALLOCATION_PCT' && mode !== 'PERCENTAGE') || (field.key === 'BITGET_FIXED_MARGIN_USDT' && mode !== 'FIXED_USDT');
            if (hidden) return null;
            const raw = text(field.key);
            const value = field.key === 'BITGET_ALLOCATION_PCT' ? (raw === '' ? '' : Number(raw) * 100) : raw;
            return <label className="form-group" key={field.key}><span className="form-label">{field.label}</span><input data-testid={`config-${field.key}`} type={field.min === undefined ? 'text' : 'number'} required={field.key in changes} placeholder="Not observed — enter to change" min={field.min} max={field.max} step={field.step} value={value} onChange={event => change(field.key, field.key === 'BITGET_ALLOCATION_PCT' && event.target.value !== '' ? String(Number(event.target.value) / 100) : event.target.value)} /><span className="text-muted text-xs">{field.help}</span></label>;
          })}
        </fieldset>
        {preview && <section aria-label="Required security confirmation">
          <h3>Confirm runtime changes</h3>
          <p>Saving applies the settings directly. No draft or separate apply step is created. Runtime changes require the security confirmation below.</p>
          <Value value={preview.effects} />
          <Value value={preview.gates} />
          <Value value={preview.warnings} />
          <fieldset disabled={busy || Boolean(operation)}>
            <label className="form-group"><span><input type="checkbox" checked={acknowledged} onChange={event => setAcknowledged(event.target.checked)} /> I have reviewed the runtime changes and safety gates.</span></label>
            {tier >= 2 && <>
              <label className="form-group"><span className="form-label">Type this confirmation: <code style={{overflowWrap: 'anywhere'}}>{String(preview.confirmation_phrase || '')}</code></span><input value={phrase} onChange={event => setPhrase(event.target.value)} autoComplete="off" /></label>
              <label className="form-group"><span className="form-label">Approval reference</span><input value={reference} onChange={event => setReference(event.target.value)} maxLength={200} placeholder="Your approved change or operational reference" /><span className="text-muted text-xs">Required by runtime-change security policy; this is not a draft ID.</span></label>
            </>}
          </fieldset>
        </section>}
        <button type="submit" className="btn btn-primary btn-sm" disabled={!ready || !allowed || busy || Boolean(operation?.jobId) || Boolean(preview && !confirmed && !operation)}>{busy ? 'Saving…' : operation ? 'Retry Save' : 'Save'}</button>
      </form>
    </section>
  );
}

export function SettingsPage({
  envelope,
  readSection,
  resourceId,
  principal,
  capabilities,
  onOpenAction,
  reload,
  render,
}: {
  envelope: Envelope | null;
  readSection: string;
  resourceId: string;
  principal: Principal | null;
  capabilities: Capability[];
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
            key={`${principal?.id}.${String(envelope?.scope.account_id)}.${String(envelope?.scope.environment)}`}
            data={isObjectRecord(data) ? data : {}}
            capability={capabilities.find(capability => capability.action === 'config.save')}
            scope={envelope?.scope || {}}
            principal={principal}
            reload={reload}
          />
          <SecretStage isOwner={isOwner} onChanged={reload} />
          <PushNotificationSettings />
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
              {readSection.startsWith('config/') && (
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
