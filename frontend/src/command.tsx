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

  if (Array.isArray(schema.oneOf)) {
    const variants = schema.oneOf.filter(isObjectRecord);
    const selected = variants.findIndex(
      (candidate) =>
        candidate.type === typeof value ||
        (candidate.type === 'integer' && typeof value === 'number')
    );
    const choice = Math.max(0, selected);
    return (
      <fieldset className="form-group fieldset-variant">
        <legend>{human(name)} input type</legend>
        <select
          aria-label={`${human(name)} input type`}
          value={choice}
          onChange={(e) =>
            onChange(variants[Number(e.target.value)]?.type === 'integer' ? 0 : '')
          }
        >
          {variants.map((variant, index) => (
            <option key={index} value={index}>
              {variant.type === 'integer'
                ? 'Numeric channel ID'
                : 'Public @channel identifier'}
            </option>
          ))}
        </select>
        <SchemaField
          name={name}
          schema={variants[choice] || {}}
          value={value}
          required={required}
          onChange={onChange}
        />
      </fieldset>
    );
  }

  if (type === 'object') {
    const properties = isObjectRecord(schema.properties) ? schema.properties : {};
    const data = isObjectRecord(value) ? value : {};
    return (
      <fieldset className="form-group fieldset-object">
        <legend>{title}</legend>
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
      </fieldset>
    );
  }

  const itemSchema = isObjectRecord(schema.items) ? schema.items : {};
  const enumItems = Array.isArray(itemSchema.enum) ? itemSchema.enum : null;

  return (
    <label className="form-group">
      <span className="form-label">
        {title}
        {required ? ' *' : ''}
      </span>
      {Array.isArray(schema.enum) ? (
        <select
          value={value === undefined ? '' : JSON.stringify(value)}
          required={required}
          onChange={(e) =>
            onChange(e.target.value ? JSON.parse(e.target.value) : undefined)
          }
        >
          <option value="">Choose…</option>
          {schema.enum.map((v) => (
            <option key={JSON.stringify(v)} value={JSON.stringify(v)}>
              {String(v)}
            </option>
          ))}
        </select>
      ) : type === 'boolean' ? (
        <select
          value={value === undefined ? '' : String(value)}
          required={required}
          onChange={(e) =>
            onChange(
              e.target.value === '' ? undefined : e.target.value === 'true'
            )
          }
        >
          <option value="">Choose…</option>
          {schema.const !== false && <option value="true">Enabled / accepted</option>}
          {schema.const !== true && <option value="false">Disabled / rejected</option>}
        </select>
      ) : type === 'array' && enumItems ? (
        <select
          multiple
          required={required}
          value={Array.isArray(value) ? value.map((v) => JSON.stringify(v)) : []}
          onChange={(e) =>
            onChange(
              Array.from(e.target.selectedOptions, (o) => JSON.parse(o.value))
            )
          }
        >
          {enumItems.map((v) => (
            <option key={JSON.stringify(v)} value={JSON.stringify(v)}>
              {String(v)}
            </option>
          ))}
        </select>
      ) : type === 'array' ? (
        <div className="array-editor">
          {(Array.isArray(value) ? value : []).map((entry, index) => (
            <div className="array-item-edit" key={index}>
              <SchemaField
                name={`${name} ${index + 1}`}
                schema={itemSchema}
                value={entry}
                required
                onChange={(v) =>
                  onChange(
                    (value as unknown[]).map((old, i) => (i === index ? v : old))
                  )
                }
              />
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={() =>
                  onChange((value as unknown[]).filter((_, i) => i !== index))
                }
              >
                Remove
              </button>
            </div>
          ))}
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            disabled={
              Array.isArray(value) &&
              value.length >= Number(schema.maxItems ?? 200)
            }
            onClick={() =>
              onChange([
                ...(Array.isArray(value) ? value : []),
                itemSchema.type === 'object'
                  ? {}
                  : itemSchema.type === 'boolean'
                  ? false
                  : '',
              ])
            }
          >
            Add item
          </button>
        </div>
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
          min={schema.minimum as number | undefined}
          max={schema.maximum as number | undefined}
          minLength={schema.minLength as number | undefined}
          maxLength={schema.maxLength as number | undefined}
          pattern={schema.pattern as string | undefined}
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
      {Boolean(schema.description) && (
        <small className="form-help">{String(schema.description)}</small>
      )}
      {schema.const !== undefined && (
        <small className="form-help">
          Fixed policy value: {String(schema.const)}. The server rejects every other value.
        </small>
      )}
      {secret && (
        <small className="form-help">
          Secret input stays in memory only; never included in URL or browser storage.
        </small>
      )}
    </label>
  );
}

export function ReviewDialog({
  isOpen,
  onClose,
  children,
}: {
  isOpen: boolean;
  onClose: () => void;
  children: React.ReactNode;
}) {
  const dialog = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    if (isOpen) {
      dialog.current?.showModal();
    } else {
      dialog.current?.close();
    }
  }, [isOpen]);

  if (!isOpen) return null;

  return (
    <dialog
      ref={dialog}
      className="review-dialog"
      aria-labelledby="preview-title"
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
    >
      <div className="dialog-header">
        <h3 id="preview-title">Preview & Safety Review</h3>
        <button
          type="button"
          className="dialog-close"
          aria-label="Close review"
          onClick={onClose}
        >
          ✕
        </button>
      </div>
      <div className="dialog-content">{children}</div>
    </dialog>
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
}: {
  capabilities: Capability[];
  scope: RecordData;
  section: string;
  items: RecordData[];
  onChanged: () => void;
  principalId: string;
  initialAction?: string;
}) {
  const [action, setAction] = useState(initialAction);
  const [args, setArgs] = useState<RecordData>({});
  const [targets, setTargets] = useState<string[]>([]);
  const [reason, setReason] = useState('');
  const [preview, setPreview] = useState<RecordData | null>(null);
  const [phrase, setPhrase] = useState('');
  const [approval, setApproval] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [job, setJob] = useState<RecordData | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const [verified, setVerified] = useState('');
  const [unaccepted, setUnaccepted] = useState(false);
  const [reviewOpen, setReviewOpen] = useState(false);

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
  const tier = Number(preview?.danger_tier ?? 0);
  const gatesPass =
    Array.isArray(preview?.gates) &&
    preview.gates.every((g) => isObjectRecord(g) && g.verdict === 'pass');

  function invalidate() {
    setPreview(null);
    setPhrase('');
    setJob(null);
    setAcknowledged(false);
    setVerified('');
    setReviewOpen(false);
  }

  function accepted(result: RecordData) {
    setJob(result);
    setPreview(null);
    sessionStorage.removeItem(recoveryStorage);
    setPending(null);
    setReviewOpen(false);
    onChanged();
  }

  async function recover() {
    if (!pending) return;
    setBusy(true);
    setError(null);
    try {
      try {
        const receipt = await api<{data: unknown}>(
          `/jobs/by-idempotency/${encodeURIComponent(pending.key)}?action=${encodeURIComponent(
            pending.action
          )}`
        );
        if (isObjectRecord(receipt.data)) {
          accepted(receipt.data);
          return;
        }
      } catch (e) {
        if (!(e instanceof ApiError && e.status === 404)) throw e;
      }
      const p = await api<RecordData>(
        `/previews/${encodeURIComponent(String(pending.payload.preview_id))}`
      );
      setPreview(p);
      setAction(pending.action);
      setReason(String(pending.payload.reason));
      setApproval(String(pending.payload.approval_reference || ''));
      setPhrase(String(pending.payload.confirmation_phrase || ''));
      setAcknowledged(false);
      setVerified('');
      setReviewOpen(true);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
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
      setReviewOpen(true);
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
      if (
        e instanceof ApiError &&
        [400, 403, 404, 409, 412, 422, 428].includes(e.status) &&
        isObjectRecord(e.detail) &&
        e.detail.code !== 'idempotency_conflict'
      ) {
        setUnaccepted(true);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card action-panel" data-testid="command-panel">
      <div className="card-header">
        <div>
          <span className="eyebrow">DURABLE OPERATIONS</span>
          <h2>Command Authorization & Queue</h2>
        </div>
      </div>
      <p className="text-secondary">
        Previews are read-only. Server rechecks ownership, freshness, gates and approval before queueing.
      </p>

      <ErrorBox error={error} />

      {pending && !job && (
        <div className="alert alert-warning" role="alert">
          <strong>An operation may already have been accepted.</strong>
          <p>
            Recover its durable receipt first. Retry uses the exact persisted public approval payload
            and original operation ID; no secrets or tokens are stored.
          </p>
          <div className="btn-row">
            <button type="button" className="btn btn-primary" disabled={busy} onClick={recover}>
              Recover same operation
            </button>
            <Link to="/audit" className="btn btn-secondary">
              Inspect durable jobs
            </Link>
            {unaccepted && (
              <button
                type="button"
                className="btn btn-secondary"
                disabled={busy}
                onClick={() => {
                  sessionStorage.removeItem(recoveryStorage);
                  setPending(null);
                  setUnaccepted(false);
                  invalidate();
                }}
              >
                Discard request proved unaccepted
              </button>
            )}
          </div>
        </div>
      )}

      <form onSubmit={makePreview}>
        <fieldset disabled={busy || !!pending} className="command-fieldset">
          <label className="form-group">
            <span className="form-label">Action selection</span>
            <select
              data-testid="action-catalog"
              value={action}
              onChange={(e) => {
                setAction(e.target.value);
                setArgs({});
                setTargets([]);
                invalidate();
              }}
            >
              <option value="">Select an action…</option>
              {capabilities.map((c) => (
                <option key={c.action} value={c.action}>
                  {c.action}
                  {!c.preview_allowed ? ' · unavailable' : ''}
                </option>
              ))}
            </select>
          </label>

          {cap && (
            <>
              <div className="capability-box">
                <div className="capability-meta">
                  <span className="badge badge-neutral">Tier {cap.danger_tier}</span>
                  <span className="muted">{cap.required_role || 'Scoped authorization'}</span>
                </div>
                <p>{cap.description}</p>
              </div>

              {cap.arguments_schema ? (
                <SchemaField
                  name="arguments"
                  schema={cap.arguments_schema}
                  value={args}
                  onChange={(v) => {
                    setArgs(isObjectRecord(v) ? v : {});
                    invalidate();
                  }}
                />
              ) : (
                <p className="muted">No editable arguments declared for this action.</p>
              )}

              {items.filter((i) => i.id !== undefined && typeof i.resource_version === 'string').length > 0 && (
                <label className="form-group">
                  <span className="form-label">Exact target records</span>
                  <select
                    data-testid="target-chooser"
                    multiple
                    value={targets}
                    onChange={(e) => {
                      setTargets(Array.from(e.target.selectedOptions, (o) => o.value));
                      invalidate();
                    }}
                  >
                    {items
                      .filter((i) => i.id !== undefined && typeof i.resource_version === 'string')
                      .map((i) => (
                        <option key={String(i.id)} value={String(i.id)}>
                          {String(i.symbol || i.display_name || i.action || section)} · {String(i.id)}
                        </option>
                      ))}
                  </select>
                  <small className="form-help">
                    Only versioned scoped records are selectable. Ownership and scope are revalidated by the server.
                  </small>
                </label>
              )}

              <label className="form-group">
                <span className="form-label">Operator reason *</span>
                <textarea
                  data-testid="operator-reason"
                  required
                  maxLength={1000}
                  value={reason}
                  placeholder="Audit reason for executing this command"
                  onChange={(e) => {
                    setReason(e.target.value);
                    invalidate();
                  }}
                />
              </label>

              <div className="form-actions">
                <button
                  type="submit"
                  disabled={busy || !cap.preview_allowed}
                  data-testid="generate-preview"
                  className="btn btn-primary"
                >
                  {busy ? 'Validating evidence…' : 'Generate Read-Only Preview'}
                </button>
              </div>
            </>
          )}
        </fieldset>
      </form>

      {preview && (
        <ReviewDialog isOpen={reviewOpen} onClose={() => setReviewOpen(false)}>
          <div className="preview" data-testid="action-preview">
            <div className="preview-meta">
              <span className={`badge ${expired ? 'badge-danger' : 'badge-neutral'}`}>
                {expired ? 'Preview Expired' : `Expires ${String(preview.expires_at)}`}
              </span>
              <span className="badge badge-neutral">Danger Tier {tier}</span>
            </div>

            <Value value={preview} />

            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={acknowledged}
                onChange={(e) => setAcknowledged(e.target.checked)}
              />
              <span>I reviewed the exact effects, exclusions, evidence gates and consequences above.</span>
            </label>

            {tier >= 2 && (
              <>
                <label className="form-group">
                  <span className="form-label">Exact confirmation phrase</span>
                  <input
                    data-testid="confirmation-phrase"
                    disabled={!!pending}
                    value={phrase}
                    onChange={(e) => setPhrase(e.target.value)}
                    autoComplete="off"
                    placeholder={String(preview.confirmation_phrase || '')}
                  />
                </label>
                <label className="form-group">
                  <span className="form-label">Approval reference *</span>
                  <input
                    disabled={!!pending}
                    required
                    maxLength={200}
                    value={approval}
                    onChange={(e) => setApproval(e.target.value)}
                    autoComplete="off"
                  />
                  <small className="form-help">
                    Actions requiring a distinct approver remain unavailable under the shared password identity.
                  </small>
                </label>
              </>
            )}

            <ErrorBox error={error} />

            <div className="dialog-actions">
              {tier >= 2 && (
                <button
                  type="button"
                  data-testid="preview-consent"
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
                  {verified === preview.preview_digest ? '✓ Digest Authorized' : 'Authorize Reviewed Digest'}
                </button>
              )}
              <button
                type="button"
                data-testid="enqueue-job"
                className="btn btn-danger"
                disabled={
                  busy ||
                  !acknowledged ||
                  Boolean(job) ||
                  (!pending &&
                    (expired ||
                      !gatesPass ||
                      !cap?.enqueue_allowed ||
                      (tier >= 2 &&
                        (phrase !== String(preview.confirmation_phrase) ||
                          !approval.trim() ||
                          verified !== preview.preview_digest))))
                }
                onClick={submit}
              >
                {pending ? 'Retrieve / Retry Same Operation' : 'Queue Approved Operation'}
              </button>
            </div>
          </div>
        </ReviewDialog>
      )}

      {job && (
        <div role="status" className="alert alert-success">
          <h3>Durable Job Queued / Accepted</h3>
          <Value value={job} />
          {Boolean(job.id) && (
            <Link to={`/audit/jobs/${encodeURIComponent(String(job.id))}`} className="btn btn-secondary btn-sm">
              Follow Job & Reconciliation →
            </Link>
          )}
        </div>
      )}
    </section>
  );
}

export function JobControls({job, onChanged}: {job: RecordData; onChanged: () => void}) {
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [verified, setVerified] = useState('');

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
      setVerified('');
      onChanged();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card job-controls">
      <div className="card-header">
        <h2>Job Authorization & Lifecycle</h2>
      </div>
      <p className="text-secondary">
        Cancellation is a request; readback determines if an in-flight effect stopped.
      </p>
      <ErrorBox error={error} />
      <div className="job-meta">
        <span>Approval digest: <code>{String(job.preview_digest).slice(0, 16)}…</code></span>
        <span>Observed revision: <code>{String(job.resource_version).slice(0, 16)}…</code></span>
      </div>
      <label className="form-group">
        <span className="form-label">Operator Reason *</span>
        <textarea
          maxLength={1000}
          value={reason}
          placeholder="Operator reason for this lifecycle decision"
          onChange={(e) => setReason(e.target.value)}
          required
        />
      </label>
      <div className="btn-row">
        {['approve', 'reject', 'cancel', 'reconcile'].map((name) => {
          const eligible =
            name === 'reconcile'
              ? job.state === 'reconciliation_pending'
              : name === 'cancel'
              ? !['succeeded', 'failed', 'partially_succeeded', 'rejected', 'expired', 'cancelled'].includes(
                  String(job.state)
                )
              : ['awaiting_approval', 'queued'].includes(String(job.state)) && !job.effect_claimed;
          return (
            <button
              data-testid={`job-${name}`}
              key={name}
              className="btn btn-secondary"
              disabled={
                busy ||
                !reason.trim() ||
                !eligible ||
                (['approve', 'reject'].includes(name) && verified !== job.preview_digest)
              }
              onClick={() => command(name)}
            >
              {human(name)}
            </button>
          );
        })}
        {['awaiting_approval', 'queued'].includes(String(job.state)) && (
          <button
            type="button"
            data-testid="job-consent"
            className="btn btn-primary"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              setError(null);
              try {
                await authorize({job_id: String(job.id)});
                setVerified(String(job.preview_digest));
                onChanged();
              } catch (e) {
                setError(e);
              } finally {
                setBusy(false);
              }
            }}
          >
            Authorize Reviewed Job Digest
          </button>
        )}
        {job.action === 'report.export' && job.state === 'succeeded' && (
          <a
            href={`/api/v1/jobs/${encodeURIComponent(String(job.id))}/download`}
            className="btn btn-secondary"
          >
            Download Authorized Export
          </a>
        )}
      </div>
    </div>
  );
}
