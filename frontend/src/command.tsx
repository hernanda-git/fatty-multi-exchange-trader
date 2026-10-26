import React, {useState, useEffect, useRef} from 'react';
import {api, ApiError, authorize} from './api';
import {Value, ErrorBox, human} from './primitives';
import {Link} from './nav';
import {type RecordData, type Capability, type PendingOperation, isObjectRecord} from './types';

export function storedOperation(key: string): PendingOperation | null {
  try {
    const raw = sessionStorage.getItem(key);
    if (!raw) return null;
    const value = JSON.parse(raw);
    if (
      isObjectRecord(value) &&
      typeof value.key === 'string' &&
      typeof value.action === 'string' &&
      isObjectRecord(value.payload)
    ) {
      return {
        key: value.key,
        action: value.action,
        payload: value.payload,
      };
    }
    return null;
  } catch {
    return null;
  }
}

export function SchemaField({
  name,
  schema,
  value,
  onChange,
  required = false,
}: {
  name: string;
  schema: RecordData;
  value: unknown;
  onChange: (v: unknown) => void;
  required?: boolean;
}) {
  const type = String(schema.type || 'string');
  const title = String(schema.title || human(name));
  const secret = /secret|password|token|credential/i.test(name) || schema.format === 'password';

  if (type === 'object') {
    const properties = isObjectRecord(schema.properties) ? schema.properties : {};
    const data = isObjectRecord(value) ? value : {};
    return (
      <div className="form-group">
        <span className="form-label">{title}</span>
        {Object.entries(properties).map(([key, s]) => (
          <SchemaField
            key={key}
            name={key}
            schema={isObjectRecord(s) ? s : {}}
            value={data[key]}
            required={Array.isArray(schema.required) && schema.required.includes(key)}
            onChange={(v) => onChange({...data, [key]: v})}
          />
        ))}
      </div>
    );
  }

  return (
    <label className="form-group">
      <span className="form-label">
        {title}
        {required ? ' *' : ''}
      </span>
      {type === 'boolean' ? (
        <select
          value={value === undefined ? '' : String(value)}
          required={required}
          onChange={(e) =>
            onChange(e.target.value === '' ? undefined : e.target.value === 'true')
          }
        >
          <option value="">Choose…</option>
          <option value="true">Enabled</option>
          <option value="false">Disabled</option>
        </select>
      ) : (
        <input
          data-testid={`argument-${name}`}
          type={
            secret
              ? 'password'
              : type === 'integer' || type === 'number'
              ? 'number'
              : schema.format === 'date-time'
              ? 'datetime-local'
              : 'text'
          }
          step={type === 'integer' ? '1' : 'any'}
          autoComplete="off"
          required={required}
          value={
            schema.format === 'date-time' && typeof value === 'string'
              ? value.replace(/Z$/, '').slice(0, 16)
              : String(value ?? '')
          }
          placeholder={`Enter ${title.toLowerCase()}`}
          onChange={(e) =>
            onChange(
              e.target.value === ''
                ? undefined
                : type === 'integer' || type === 'number'
                ? Number(e.target.value)
                : schema.format === 'date-time'
                ? new Date(e.target.value + 'Z').toISOString()
                : e.target.value
            )
          }
        />
      )}
    </label>
  );
}

export function ActionPanel({
  capabilities,
  scope,
  section,
  items,
  onChanged,
  principalId,
  initialAction = '',
  initialArgs = {},
  initialTargets = [],
  isOpen,
  onClose,
}: {
  capabilities: Capability[];
  scope: RecordData;
  section: string;
  items: RecordData[];
  onChanged: () => void;
  principalId: string;
  initialAction?: string;
  initialArgs?: RecordData;
  initialTargets?: string[];
  isOpen: boolean;
  onClose: () => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);

  const [action, setAction] = useState(initialAction);
  const [args, setArgs] = useState<RecordData>(initialArgs);
  const [targets, setTargets] = useState<string[]>(initialTargets);
  const [reason, setReason] = useState('');
  const [preview, setPreview] = useState<RecordData | null>(null);
  const [phrase, setPhrase] = useState('');
  const [approval, setApproval] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [job, setJob] = useState<RecordData | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const [verified, setVerified] = useState('');

  useEffect(() => {
    if (initialAction) {
      setAction(initialAction);
      setArgs(initialArgs || {});
      setTargets(initialTargets || []);
    }
  }, [initialAction, initialArgs, initialTargets]);

  useEffect(() => {
    if (isOpen) {
      dialogRef.current?.showModal();
    } else {
      dialogRef.current?.close();
    }
  }, [isOpen]);

  const recoveryStorage = `console.pending.${principalId}.${String(
    scope.account_id
  )}.${String(scope.environment)}.${section}`;
  const [pending, setPending] = useState<PendingOperation | null>(() =>
    storedOperation(recoveryStorage)
  );

  const cap = capabilities.find((c) => c.action === action);
  const expired = preview?.expires_at
    ? Date.parse(String(preview.expires_at)) <= Date.now()
    : true;
  const tier = Number(preview?.danger_tier ?? cap?.danger_tier ?? 0);
  const gatesPass =
    Array.isArray(preview?.gates) &&
    preview.gates.every((g) => isObjectRecord(g) && g.verdict === 'pass');

  function reset() {
    setPreview(null);
    setPhrase('');
    setJob(null);
    setAcknowledged(false);
    setVerified('');
    setError(null);
    onClose();
  }

  function accepted(result: RecordData) {
    setJob(result);
    setPreview(null);
    sessionStorage.removeItem(recoveryStorage);
    setPending(null);
    onChanged();
  }

  async function makePreview(e: React.FormEvent) {
    e.preventDefault();
    if (pending) return;
    setError(null);
    setBusy(true);
    try {
      const refs = targets.map((id) => {
        const item = items.find((i) => String(i.id) === id);
        return {
          kind: section,
          id,
          resource_version: item?.resource_version,
        };
      });
      const p = await api<RecordData>('/previews', {
        action,
        arguments: args,
        target_refs: refs,
        reason,
        scope,
        policy_version: cap?.policy_version,
      });
      setPreview(p);
      setPhrase('');
      setAcknowledged(false);
      setVerified('');
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  async function submit() {
    if (!preview) return;
    setBusy(true);
    setError(null);
    try {
      const operation =
        pending || {
          key: crypto.randomUUID(),
          action,
          payload: {
            preview_id: preview.id,
            preview_digest: preview.preview_digest,
            confirmation_phrase: phrase,
            reason,
            approval_reference: approval || null,
            policy_version: preview.policy_version,
            resource_version: preview.resource_version,
          },
        };
      sessionStorage.setItem(recoveryStorage, JSON.stringify(operation));
      setPending(operation);
      const result = await api<RecordData>(
        '/jobs',
        operation.payload,
        {
          'Idempotency-Key': operation.key,
          'If-Match': String(operation.payload.resource_version),
        }
      );
      accepted(result);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  if (!isOpen) return null;

  return (
    <dialog
      ref={dialogRef}
      className="action-modal"
      data-testid="command-panel"
      onCancel={(e) => {
        e.preventDefault();
        reset();
      }}
    >
      <div className="modal-header">
        <div>
          <span className="eyebrow">DURABLE COMMAND</span>
          <h3>{action ? human(action) : 'Execute Action'}</h3>
        </div>
        <button type="button" className="modal-close-btn" onClick={reset} aria-label="Close">
          ✕
        </button>
      </div>

      <div className="modal-body">
        <ErrorBox error={error} />

        {!preview && (
          <form onSubmit={makePreview}>
            <label className="form-group">
              <span className="form-label">Action</span>
              <select
                data-testid="action-catalog"
                value={action}
                onChange={(e) => {
                  setAction(e.target.value);
                  setArgs({});
                  setTargets([]);
                  setPreview(null);
                }}
              >
                <option value="">Select an action…</option>
                {capabilities.map((c) => (
                  <option key={c.action} value={c.action}>
                    {c.action} {!c.preview_allowed ? '(unavailable)' : ''}
                  </option>
                ))}
              </select>
            </label>

            {cap && (
              <>
                <div style={{background: 'var(--bg-subtle)', padding: '10px 14px', borderRadius: '6px', margin: '12px 0'}}>
                  <div style={{display: 'flex', gap: '8px', alignItems: 'center', marginBottom: '4px'}}>
                    <span className="badge badge-warn">Tier {cap.danger_tier}</span>
                    <span className="text-xs text-muted">{cap.required_role || 'Operator'}</span>
                  </div>
                  <p className="text-secondary text-sm">{cap.description}</p>
                </div>

                {cap.arguments_schema && (
                  <SchemaField
                    name="arguments"
                    schema={cap.arguments_schema}
                    value={args}
                    onChange={(v) => {
                      setArgs(isObjectRecord(v) ? v : {});
                      setPreview(null);
                    }}
                  />
                )}

                <label className="form-group">
                  <span className="form-label">Operator Audit Reason *</span>
                  <textarea
                    data-testid="operator-reason"
                    required
                    maxLength={1000}
                    value={reason}
                    placeholder="Enter reason for audit logs"
                    onChange={(e) => {
                      setReason(e.target.value);
                      setPreview(null);
                    }}
                  />
                </label>
              </>
            )}

            <div className="modal-footer" style={{padding: '16px 0 0', background: 'transparent'}}>
              <button type="button" className="btn btn-secondary" onClick={reset}>
                Cancel
              </button>
              <button
                type="submit"
                className="btn btn-primary"
                disabled={busy || !action || !cap?.preview_allowed}
                data-testid="generate-preview"
              >
                {busy ? 'Validating…' : 'Generate Preview →'}
              </button>
            </div>
          </form>
        )}

        {preview && (
          <div data-testid="action-preview">
            <div style={{display: 'flex', gap: '8px', marginBottom: '12px'}}>
              <span className={`badge ${expired ? 'badge-danger' : 'badge-neutral'}`}>
                {expired ? 'Preview Expired' : 'Preview Valid'}
              </span>
              <span className="badge badge-warn">Danger Tier {tier}</span>
            </div>

            <div style={{background: 'var(--bg-subtle)', padding: '12px', borderRadius: '6px', marginBottom: '14px', fontSize: '12.5px'}}>
              <Value value={preview} compact />
            </div>

            <label style={{display: 'flex', gap: '8px', alignItems: 'flex-start', margin: '12px 0', fontSize: '13px', cursor: 'pointer'}}>
              <input
                type="checkbox"
                checked={acknowledged}
                onChange={(e) => setAcknowledged(e.target.checked)}
                style={{marginTop: '2px'}}
              />
              <span>I acknowledge the exact server effects, exclusions and risk gates above.</span>
            </label>

            {tier >= 2 && (
              <>
                <label className="form-group">
                  <span className="form-label">Confirmation Phrase: <code>{String(preview.confirmation_phrase || '')}</code></span>
                  <input
                    data-testid="confirmation-phrase"
                    value={phrase}
                    onChange={(e) => setPhrase(e.target.value)}
                    placeholder="Type phrase exactly"
                  />
                </label>
                <label className="form-group">
                  <span className="form-label">Approval Reference *</span>
                  <input
                    value={approval}
                    onChange={(e) => setApproval(e.target.value)}
                    placeholder="Approval ticket / operational reference"
                    required
                  />
                </label>
              </>
            )}

            <div className="modal-footer" style={{padding: '16px 0 0', background: 'transparent'}}>
              <button type="button" className="btn btn-secondary" onClick={() => setPreview(null)}>
                ← Back
              </button>

              {tier >= 2 && (
                <button
                  type="button"
                  className="btn btn-secondary"
                  disabled={busy || expired || !acknowledged}
                  onClick={async () => {
                    setBusy(true);
                    setError(null);
                    try {
                      await authorize({preview_digest: String(preview.preview_digest)});
                      setVerified(String(preview.preview_digest));
                      onChanged();
                    } catch (e) {
                      setError(e);
                    } finally {
                      setBusy(false);
                    }
                  }}
                >
                  {verified === preview.preview_digest ? '✓ Authorized' : 'Authorize Digest'}
                </button>
              )}

              <button
                type="button"
                className="btn btn-danger"
                disabled={
                  busy ||
                  !acknowledged ||
                  (tier >= 2 &&
                    (phrase !== String(preview.confirmation_phrase) ||
                      !approval.trim() ||
                      verified !== preview.preview_digest))
                }
                onClick={submit}
              >
                {busy ? 'Queueing…' : 'Queue Approved Command'}
              </button>
            </div>
          </div>
        )}

        {job && (
          <div className="alert alert-success" style={{marginTop: '16px'}}>
            <strong>Command Queued: Job #{String(job.id || '')}</strong>
            <div style={{marginTop: '8px'}}>
              <Link to={`/audit/jobs/${encodeURIComponent(String(job.id))}`} className="btn btn-primary btn-sm">
                Follow Job Progress →
              </Link>
            </div>
          </div>
        )}
      </div>
    </dialog>
  );
}

export function JobControls({job, onChanged}: {job: RecordData; onChanged: () => void}) {
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function command(name: string) {
    setBusy(true);
    setError(null);
    try {
      await api(
        `/jobs/${encodeURIComponent(String(job.id))}/${name}`,
        {
          preview_digest: job.preview_digest,
          resource_version: job.resource_version,
          reason,
        },
        {'If-Match': String(job.resource_version)}
      );
      onChanged();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card" style={{marginTop: '16px'}}>
      <div className="card-header">
        <h3>Job Lifecycle Controls</h3>
      </div>
      <ErrorBox error={error} />
      <div className="btn-row">
        {['approve', 'reject', 'cancel', 'reconcile'].map((name) => (
          <button
            key={name}
            type="button"
            className="btn btn-secondary btn-sm"
            disabled={busy}
            onClick={() => command(name)}
          >
            {human(name)}
          </button>
        ))}
      </div>
    </div>
  );
}
