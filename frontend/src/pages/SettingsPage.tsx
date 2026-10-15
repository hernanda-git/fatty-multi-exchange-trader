import React from 'react';
import {DomainTable, DomainDetail} from '../domain';
import {SecretStage} from '../forms';
import {Link} from '../nav';
import type {Envelope, Capability, RecordData, Principal} from '../types';
import {isObjectRecord} from '../types';

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
      {/* Contextual top controls based on settings domain */}
      {readSection === 'config' && (
        <SecretStage isOwner={isOwner} onChanged={reload} />
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
