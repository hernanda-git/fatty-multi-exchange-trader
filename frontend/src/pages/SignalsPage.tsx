import React from 'react';
import {Link} from '../nav';
import {DomainTable, DomainDetail} from '../domain';
import type {Envelope, Capability, RecordData} from '../types';
import {isObjectRecord} from '../types';

export function SignalsPage({
  envelope,
  readSection,
  resourceId,
  onOpenAction,
  render,
}: {
  envelope: Envelope | null;
  readSection: string;
  resourceId: string;
  onOpenAction: (action: string, initialArgs?: RecordData, initialTargets?: string[]) => void;
  render: (value: unknown) => React.ReactNode;
}) {
  const data = envelope?.data;
  const items = isObjectRecord(data) && Array.isArray(data.items) ? data.items.filter(isObjectRecord) : [];

  return (
    <div className="page-content signals-page">
      {resourceId ? (
        <DomainDetail section={readSection} data={data} render={render} />
      ) : (
        <section className="card">
          <div className="card-header">
            <div>
              <h2>{readSection === 'dispatches' ? 'Dispatches & Outcomes (UX07)' : 'Signals Inbox (UX06)'}</h2>
              <p className="text-secondary">
                Immutable intake stream from Telegram source channels. Revisions, parsed trade setups, and
                downstream dispatch telemetry are preserved without client-side mutation.
              </p>
            </div>
            <div className="header-actions">
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={() => onOpenAction('signal.reanalyze')}
              >
                Reanalyze Signal
              </button>
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={() => onOpenAction('signal.annotate')}
              >
                Annotate Signal
              </button>
            </div>
          </div>

          <DomainTable section={readSection} items={items} render={render} />
        </section>
      )}
    </div>
  );
}
