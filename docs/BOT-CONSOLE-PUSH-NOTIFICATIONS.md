# Push notifications (iPhone / desktop PWA)

The console delivers the same operator events as the Telegram outbox to browser
push subscriptions. Delivery is durable: every `(notification, subscription)` pair
gets its own ledger row in `web_push_deliveries` with bounded retries, and
subscriptions the push service reports permanently gone (HTTP 404/410) are
disabled automatically. A push is transport evidence only — never a trade
confirmation.

## What you receive

- analyzed signals, dispatch transitions (queued / preflight / filled / rejected /
  expired), dedicated rejection alerts
- kill-switch and latch events, latch-recovery outcomes
- console job outcomes and health cards queued through the outbox
- explicit test sends from Settings

## iPhone setup (iOS 16.4+)

1. Open `https://trade.warga-digital.com` in **Safari**.
2. Share → **Add to Home Screen**.
3. Launch the console from the new Home Screen icon — Web Push on iOS only works
   for installed Home Screen web apps, not plain Safari tabs.
4. Sign in and open **Settings → Configuration → Push notifications**.
5. Tap **Enable on this device**, allow notifications, then **Send test**.

Desktop Chrome/Edge/Firefox work without installation; system notification
permission may need to be granted for the browser once.

## Operations

- **VAPID keys**: `runtime/web-push-private.pem` (0600, mounted read-only into
  `notification-sender` as `/run/web-push-private.pem`) and
  `runtime/web-push-public.txt` (0644, mounted read-only into `web` as
  `/run/web-push-public.txt` and served to browsers). Generate with
  `python scripts/provision_web_push.py`; inspect with `--check`.
- **Rotating** the keypair invalidates every existing subscription; devices must
  subscribe again from the console. Unsubscribe through the UI so the disabled
  row stays as audit evidence.
- **Compose**: `WEB_PUSH_VAPID_SUBJECT` (default `https://trade.warga-digital.com`),
  `WEB_PUSH_TTL_SECONDS` (default 3600), `WEB_PUSH_MAX_ATTEMPTS` (5),
  `WEB_PUSH_RETRY_SECONDS` (60), `WEB_PUSH_BATCH_SIZE` (10).
- **Schema**: migrations 28 and 29 add `web_push_subscriptions` and
  `web_push_deliveries` (additive; applied by the `migrate` service) and grant
  the console role `SELECT/INSERT/UPDATE` on subscriptions plus `INSERT` on
  `notifications_outbox` for the test action. The sender runs as the
  application role, which already owns full access.
- **API**: `GET /api/v1/push/config`, `GET/POST /api/v1/push/subscriptions`,
  `POST /api/v1/push/subscriptions/remove`, `POST /api/v1/push/test`. All writes
  require the session, the exact origin and CSRF; every call is audited.
- **Failure states**: `pending` retries with backoff up to the attempts cap, then
  `failed`; `sent` means the push service accepted the message, not that the
  device displayed it. Finished deliveries are pruned after 30 days by the sender.
- The six-hour health card and other direct-send scripts still target Telegram;
  only outbox events fan out to push.
