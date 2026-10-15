import React from 'react';
import {DomainTable, DomainDetail} from '../domain';
import {JobControls} from '../command';
import type {Envelope, Capability, RecordData} from '../types';
import {isObjectRecord} from '../types';

export function ActivityPage({
  envelope,
  readSection,
  resourceId,
  onOpenAction,
  reload,
  render,
}: {
  envelope: Envelope | null;
  readSection: string;
  resourceId: string;
  onOpenAction: (action: string, initialArgs?: RecordData, initialTargets?: string[]) => void;
  reload: () => void;
  render: (value: unknown) => React.ReactNode;
}) {
  const data = envelope?.data;
  const items = isObjectRecord(data) && Array.isArray(data.items) ? data.items.filter(isObjectRecord) : [];

  const getTitle = () => {
    switch (readSection) {
      case 'jobs':
        return 'Durable Command Jobs (UX09)';
      case 'approvals':
        return 'Approvals & Tier 3 Governance (UX08/UX09)';
      case 'management':
        return 'Trade Management Instructions (UX08)';
      case 'notifications':
        return 'Notification Deliveries & Outbox (UX10)';
      default:
        return 'System Audit Trail (UX08)';
    }
  };

  return (
    <div className="page-content activity-page">
      {resourceId ? (
        <div className="detail-stack">
          <DomainDetail section={readSection} data={data} render={render} />
          {readSection === 'jobs' && isObjectRecord(data) && (
            <JobControls job={data} onChanged={reload} />
          )}
        </div>
      ) : (
        <section className="card">
          <div className="card-header">
            <div>
              <h2>{getTitle()}</h2>
              <p className="text-secondary">
                Immutable operational ledger. Every command, state transition, and notification dispatch is
                recorded with provenance, actor identities, and correlation IDs.
              </p>
            </div>
            <div className="header-actions">
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={() => onOpenAction('report.export')}
              >
                Export Audit Report
              </button>
              {readSection === 'notifications' && (
                <>
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    onClick={() => onOpenAction('notification.test')}
                  >
                    Test Notification
                  </button>
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    onClick={() => onOpenAction('notification.configure_schedule')}
                  >
                    Configure Schedule
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
