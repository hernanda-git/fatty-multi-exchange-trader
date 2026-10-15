import React, {useEffect, useState} from 'react';
import {api, ApiError} from '../api';
import {ErrorBox, Value} from '../primitives';
import type {RecordData} from '../types';
import {isObjectRecord} from '../types';
import {
  disablePushSubscription,
  enablePushSubscription,
  existingSubscription,
  isStandalone,
  pushSupported,
  registerPushServiceWorker,
  subscriptionRegistration,
} from '../push';

const DEVICE_KEY = 'console.push.subscription.id';

export function PushNotificationSettings() {
  const [supported] = useState(() => pushSupported());
  const [config, setConfig] = useState<RecordData | null>(null);
  const [items, setItems] = useState<RecordData[]>([]);
  const [enabledCount, setEnabledCount] = useState(0);
  const [deviceSubscribed, setDeviceSubscribed] = useState(false);
  const [permission, setPermission] = useState<string>(
    typeof Notification !== 'undefined' ? Notification.permission : 'unsupported'
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [status, setStatus] = useState('');

  const load = async () => {
    try {
      const result = await api<RecordData>('/push/config');
      if (isObjectRecord(result.data)) setConfig(result.data);
      const list = await api<RecordData>('/push/subscriptions');
      if (isObjectRecord(list.data)) {
        setItems(Array.isArray(list.data.items) ? list.data.items.filter(isObjectRecord) : []);
        setEnabledCount(Number(list.data.enabled_count ?? 0));
      }
      setDeviceSubscribed(Boolean(await existingSubscription()));
    } catch (err) {
      setError(err);
    }
  };

  useEffect(() => {
    void registerPushServiceWorker();
    void load();
  }, []);

  const serverEnabled = config?.enabled === true;
  const publicKey = typeof config?.public_key === 'string' ? config.public_key : '';

  async function enable() {
    setBusy(true);
    setError(null);
    setStatus('');
    try {
      if (typeof Notification === 'undefined') throw new Error('Notifications unavailable');
      const decided = await Notification.requestPermission();
      setPermission(decided);
      if (decided !== 'granted') {
        setStatus('Notification permission was not granted.');
        return;
      }
      const subscription = await enablePushSubscription(publicKey);
      const registration = subscriptionRegistration(subscription);
      const saved = await api<RecordData>('/push/subscriptions', registration);
      if (isObjectRecord(saved.data) && typeof saved.data.id === 'string') {
        localStorage.setItem(DEVICE_KEY, saved.data.id);
      }
      setDeviceSubscribed(true);
      setStatus('This device is subscribed. Use “Send test” to confirm delivery.');
      await load();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  async function disable() {
    setBusy(true);
    setError(null);
    setStatus('');
    try {
      await disablePushSubscription();
      const id = localStorage.getItem(DEVICE_KEY);
      if (id) {
        try {
          await api<RecordData>('/push/subscriptions/remove', {id});
        } catch (err) {
          if (!(err instanceof ApiError) || err.status !== 404) throw err;
        }
        localStorage.removeItem(DEVICE_KEY);
      }
      setDeviceSubscribed(false);
      setStatus('This device was unsubscribed.');
      await load();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  async function removeRow(id: string) {
    setBusy(true);
    setError(null);
    try {
      await api<RecordData>('/push/subscriptions/remove', {id});
      await load();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  async function sendTest() {
    setBusy(true);
    setError(null);
    setStatus('');
    try {
      await api<RecordData>('/push/test', {});
      setStatus('Test notification queued. It should arrive within a few seconds.');
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card" data-testid="push-notifications">
      <div className="card-header">
        <div>
          <span className="eyebrow">DELIVERY</span>
          <h2>Push notifications (iPhone / desktop)</h2>
          <p className="text-secondary">
            Alerts are delivered to devices subscribed here with the same events as the Telegram outbox:
            analyzed signals, dispatch transitions, trade alerts, safety events and reports.
          </p>
        </div>
      </div>
      <ErrorBox error={error} />
      {status && <p className="text-secondary" data-testid="push-status">{status}</p>}
      {!supported && (
        <p className="text-muted">
          This browser does not expose the Web Push APIs. On iPhone, open the console in Safari
          (iOS 16.4+), use Share → “Add to Home Screen”, then open it from the Home Screen icon.
        </p>
      )}
      {supported && !serverEnabled && (
        <p className="alert alert-info">
          The server has no VAPID key configured yet, so push delivery is disabled. Ask the operator
          to provision <code>runtime/web-push-private.pem</code> / <code>runtime/web-push-public.txt</code>.
        </p>
      )}
      {supported && serverEnabled && !isStandalone() && (
        <p className="alert alert-info" data-testid="ios-install-hint">
          On iPhone, notifications require the installed app: open in Safari, tap Share →
          “Add to Home Screen”, then launch the console from that icon and subscribe here.
        </p>
      )}
      <div className="header-actions" style={{marginBottom: '12px'}}>
        <button
          type="button"
          className="btn btn-primary btn-sm"
          disabled={busy || !serverEnabled || deviceSubscribed}
          onClick={() => void enable()}
          data-testid="push-enable"
        >
          {deviceSubscribed ? 'Subscribed on this device' : 'Enable on this device'}
        </button>
        <button
          type="button"
          className="btn btn-secondary btn-sm"
          disabled={busy || !deviceSubscribed}
          onClick={() => void disable()}
          data-testid="push-disable"
        >
          Unsubscribe this device
        </button>
        <button
          type="button"
          className="btn btn-secondary btn-sm"
          disabled={busy || enabledCount === 0}
          onClick={() => void sendTest()}
          data-testid="push-test"
        >
          Send test
        </button>
      </div>
      <p className="text-muted text-xs">
        Permission: {permission} · enabled subscriptions: {enabledCount}
      </p>
      <table className="table">
        <thead>
          <tr>
            <th>Device</th>
            <th>Registered</th>
            <th>Last seen</th>
            <th>State</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {items.length === 0 && (
            <tr>
              <td colSpan={5} className="text-muted">No subscribed devices yet.</td>
            </tr>
          )}
          {items.map(item => (
            <tr key={String(item.id)}>
              <td className="text-xs">{String(item.user_agent ?? 'unknown device')}</td>
              <td><Value value={item.created_at} /></td>
              <td><Value value={item.last_seen_at} /></td>
              <td>{item.disabled_at ? 'disabled' : 'enabled'}</td>
              <td>
                {!item.disabled_at && (
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    disabled={busy}
                    onClick={() => void removeRow(String(item.id))}
                  >
                    Remove
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
