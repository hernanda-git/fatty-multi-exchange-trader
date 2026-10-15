export function pushSupported(): boolean {
  return (
    typeof window !== 'undefined' &&
    'serviceWorker' in navigator &&
    'PushManager' in window &&
    'Notification' in window
  );
}

export function isStandalone(): boolean {
  if (typeof window === 'undefined') return false;
  // iOS Safari exposes a non-standard `standalone` flag on navigator; lib.dom cannot express it.
  const iosNavigator = window.navigator as Navigator & {standalone?: boolean};
  return (
    window.matchMedia?.('(display-mode: standalone)').matches === true ||
    iosNavigator.standalone === true
  );
}

export async function registerPushServiceWorker(): Promise<ServiceWorkerRegistration | null> {
  if (typeof navigator === 'undefined' || !('serviceWorker' in navigator)) return null;
  try {
    return await navigator.serviceWorker.register('/sw.js');
  } catch {
    return null;
  }
}

export function urlBase64ToUint8Array(value: string): Uint8Array<ArrayBuffer> {
  const padding = '='.repeat((4 - (value.length % 4)) % 4);
  const normalized = (value + padding).replace(/-/g, '+').replace(/_/g, '/');
  const raw = atob(normalized);
  const bytes = new Uint8Array(new ArrayBuffer(raw.length));
  for (let index = 0; index < raw.length; index += 1) bytes[index] = raw.charCodeAt(index);
  return bytes;
}

export async function existingSubscription(): Promise<PushSubscription | null> {
  if (typeof navigator === 'undefined' || !('serviceWorker' in navigator)) return null;
  const registration = await navigator.serviceWorker.getRegistration();
  if (!registration) return null;
  return registration.pushManager.getSubscription();
}

export async function enablePushSubscription(publicKey: string): Promise<PushSubscription> {
  const registration = (await navigator.serviceWorker.getRegistration()) ??
    (await registerPushServiceWorker());
  if (!registration) throw new Error('Service worker registration failed');
  const ready = await navigator.serviceWorker.ready;
  const existing = await ready.pushManager.getSubscription();
  if (existing) return existing;
  const applicationServerKey = urlBase64ToUint8Array(publicKey);
  return ready.pushManager.subscribe({userVisibleOnly: true, applicationServerKey});
}

export async function disablePushSubscription(): Promise<void> {
  const subscription = await existingSubscription();
  if (subscription) await subscription.unsubscribe();
}

export function subscriptionRegistration(subscription: PushSubscription): {
  endpoint: string;
  p256dh: string;
  auth: string;
  user_agent: string;
} {
  const json = subscription.toJSON();
  return {
    endpoint: subscription.endpoint,
    p256dh: String(json.keys?.p256dh ?? ''),
    auth: String(json.keys?.auth ?? ''),
    user_agent: navigator.userAgent.slice(0, 200),
  };
}
