import {useState, useEffect, useRef} from 'react';
import {api, type RecordData} from './api';

export const serverFilters: Record<string, string[]> = {
  signals: [
    'source',
    'channel_id',
    'message_id',
    'state',
    'intake_state',
    'symbol',
    'direction',
    'revision',
    'dispatch_state',
    'terminal_reason',
    'has_media',
    'classification',
  ],
  dispatches: [
    'source_id',
    'source',
    'symbol',
    'direction',
    'revision',
    'state',
    'terminal_reason',
    'exchange',
  ],
  orders: [
    'symbol',
    'role',
    'state',
    'client_order_id',
    'provider_order_id',
    'dispatch_id',
    'entry_leg',
    'order_type',
    'ownership',
  ],
  fills: [
    'symbol',
    'client_order_id',
    'provider_fill_id',
    'intent_id',
    'ownership',
    'source',
    'fee_ccy',
  ],
  management: ['symbol', 'state', 'action', 'source_message_id', 'revision'],
  jobs: [
    'state',
    'action',
    'actor_id',
    'resource_id',
    'channel',
    'approval_reference',
  ],
  approvals: ['job_id', 'actor_id', 'approver_id', 'state'],
  audit: [
    'action',
    'actor_id',
    'job_id',
    'resource_id',
    'request_id',
    'channel',
    'outcome',
  ],
  notifications: [
    'state',
    'event_kind',
    'dedup_key',
    'job_id',
    'source_id',
    'intent_id',
  ],
  positions: ['symbol', 'direction', 'ownership', 'protection_state', 'state'],
  protection: [
    'symbol',
    'direction',
    'ownership',
    'native_state',
    'fallback_state',
    'stream_state',
    'missing_sl',
    'missing_tp',
  ],
  services: ['state', 'service_id'],
  backups: ['state', 'release_id', 'verified'],
  releases: ['state', 'approved'],
  config: ['kind', 'state'],
  'config/history': ['kind', 'state'],
  'config/drafts': ['kind', 'state'],
  'recovery/baselines': ['approval_reference', 'record_kind', 'record_id'],
  'account/reservations': ['symbol', 'state', 'client_order_id', 'dispatch_id'],
  'account/admission-snapshots': ['margin_coin'],
  'account/pnl': [
    'symbol',
    'client_order_id',
    'ownership',
    'fee_ccy',
    'source',
    'intent_id',
  ],
  'access/principals': ['enabled', 'role'],
  'access/sessions': ['principal_id', 'revoked'],
  account: ['symbol', 'ownership'],
};

export function Filters({
  section,
  value,
  onApply,
}: {
  section: string;
  value: Record<string, string>;
  onApply: (value: Record<string, string>) => void;
}) {
  const [draft, setDraft] = useState<Record<string, string>>(value);
  const [expanded, setExpanded] = useState(false);
  const apply = useRef(onApply);
  apply.current = onApply;
  const observed = useRef(JSON.stringify(value));

  useEffect(() => {
    setDraft(value);
    observed.current = JSON.stringify(value);
  }, [value]);

  useEffect(() => {
    if (JSON.stringify(draft) === observed.current) return;
    const timer = setTimeout(() => {
      observed.current = JSON.stringify(draft);
      apply.current(draft);
    }, 400);
    return () => clearTimeout(timer);
  }, [draft]);

  const activeCount = Object.keys(value).filter((k) => value[k]).length;
  const specificFields = serverFilters[section] || [];

  return (
    <div className="filters-container">
      <form
        className="filters-form"
        data-testid="server-filters"
        onSubmit={(e) => {
          e.preventDefault();
          observed.current = JSON.stringify(draft);
          onApply(draft);
        }}
      >
        <div className="filters-quick-row">
          <label className="search-field">
            <span className="sr-only">Search records</span>
            <input
              type="search"
              value={draft.search || ''}
              onChange={(e) => setDraft({...draft, search: e.target.value})}
              placeholder="Search server records…"
            />
          </label>

          <label className="sort-field">
            <span className="sr-only">Sort order</span>
            <select
              value={draft.sort || 'newest'}
              onChange={(e) => setDraft({...draft, sort: e.target.value})}
            >
              <option value="newest">Newest first</option>
              <option value="oldest">Oldest first</option>
            </select>
          </label>

          {specificFields.length > 0 && (
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() => setExpanded(!expanded)}
              aria-expanded={expanded}
            >
              {expanded ? 'Fewer filters' : `Filters ${activeCount ? `(${activeCount})` : ''}`}
            </button>
          )}

          {activeCount > 0 && (
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() => {
                setDraft({});
                observed.current = '{}';
                onApply({});
              }}
            >
              Clear
            </button>
          )}
        </div>

        {expanded && (
          <div className="filters-expanded-grid">
            {specificFields.map((field) => (
              <label key={field} className="form-group form-group-sm">
                <span className="form-label">{field.replace(/_/g, ' ')}</span>
                <input
                  value={draft[field] || ''}
                  onChange={(e) => setDraft({...draft, [field]: e.target.value})}
                />
              </label>
            ))}
            <label className="form-group form-group-sm">
              <span className="form-label">From (UTC)</span>
              <input
                type="datetime-local"
                value={draft.from ? draft.from.replace(/Z$/, '').slice(0, 16) : ''}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    from: e.target.value ? new Date(e.target.value + 'Z').toISOString() : '',
                  })
                }
              />
            </label>
            <label className="form-group form-group-sm">
              <span className="form-label">To (UTC)</span>
              <input
                type="datetime-local"
                value={draft.to ? draft.to.replace(/Z$/, '').slice(0, 16) : ''}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    to: e.target.value ? new Date(e.target.value + 'Z').toISOString() : '',
                  })
                }
              />
            </label>
          </div>
        )}
      </form>
    </div>
  );
}

export function SecretStage({
  isOwner,
  onChanged,
}: {
  isOwner: boolean;
  onChanged: () => void;
}) {
  const [kind, setKind] = useState('BITGET_API_KEY');
  const [secret, setSecret] = useState('');
  const [result, setResult] = useState<RecordData | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  return (
    <section className="card secret-stage-card">
      <div className="card-header">
        <div>
          <span className="eyebrow">ENCRYPTED CREDENTIAL STAGING</span>
          <h2>Stage Replacement Secret</h2>
        </div>
      </div>
      <p className="text-secondary">
        Staging does not apply or activate credentials. Use the returned opaque stage ID in the
        separately reviewed configuration draft. Secret input is cleared from memory on submission.
      </p>

      {!isOwner ? (
        <div className="alert alert-warning" role="status">
          Policy: only the password-authenticated owner may stage secrets.
        </div>
      ) : (
        <form
          data-testid="secret-stage"
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            setError('');
            setResult(null);
            try {
              const r = await api<RecordData>('/secret-stages', {kind, value: secret});
              setSecret('');
              setResult(r);
              onChanged();
            } catch (err) {
              setError(err instanceof Error ? err.message : 'Secret staging failed.');
            } finally {
              setBusy(false);
            }
          }}
        >
          <div className="form-row">
            <label className="form-group">
              <span className="form-label">Credential kind</span>
              <select
                value={kind}
                onChange={(e) => {
                  setKind(e.target.value);
                  setSecret('');
                  setResult(null);
                }}
              >
                {[
                  'BITGET_API_KEY',
                  'BITGET_API_SECRET',
                  'BITGET_API_PASSPHRASE',
                  'TG_BOT_TOKEN',
                ].map((k) => (
                  <option key={k} value={k}>
                    {k}
                  </option>
                ))}
              </select>
            </label>

            <label className="form-group flex-1">
              <span className="form-label">Replacement secret</span>
              <input
                type="password"
                autoComplete="off"
                required
                maxLength={4096}
                value={secret}
                onChange={(e) => setSecret(e.target.value)}
                placeholder="Enter secret to stage in encrypted storage"
              />
            </label>
          </div>

          <div className="form-actions">
            <button disabled={busy} type="submit" className="btn btn-primary">
              {busy ? 'Staging secret…' : 'Stage Replacement Secret'}
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => {
                setSecret('');
                setResult(null);
                setError('');
              }}
            >
              Clear
            </button>
          </div>
        </form>
      )}

      {error && (
        <div className="alert alert-danger" role="alert">
          {error}
        </div>
      )}

      {result && (
        <div className="alert alert-success" role="status">
          <strong>Encrypted stage created. No configuration was applied.</strong>
          <dl className="record-grid">
            {Object.entries(result)
              .filter(([key]) => ['stage_id', 'kind', 'expires_at'].includes(key))
              .map(([key, value]) => (
                <div key={key}>
                  <dt>{key}</dt>
                  <dd>
                    <code>{String(value)}</code>
                  </dd>
                </div>
              ))}
          </dl>
        </div>
      )}
    </section>
  );
}
