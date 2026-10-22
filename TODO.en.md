# TODO

[🇮🇹 Italiano](TODO.md) · 🇬🇧 English

Ideas for future ExpenseLens releases, in recommended priority order. These are priorities to
verify: no performance or load measurements have been taken.

## Recommended order
1. Automated tests and CI (frontend and end-to-end), structured logs, Sentry, verified backups.
2. Password reset, email verification and GDPR features.
3. Redis and workers for heavy jobs, S3 storage, load testing (k6).
4. Plans and billing with Stripe, then an admin panel.
5. Bank connection and automatic categorisation.

## 1. Foundations for selling: multi-tenancy and reliability
- [ ] Tenant isolation: automated tests against data leaks between households and, on Postgres, row-level security.
- [ ] Stateless application: Redis for rate limits, cache and queues; shared S3 storage for documents.
- [ ] Queues for heavy jobs (OCR, notifications, summaries, backups) with a dedicated worker (arq or Celery).
- [ ] Observability: structured logs, Prometheus/Grafana metrics, Sentry, health checks and alerts.
- [ ] CI/CD: frontend and end-to-end tests (Playwright), dependency scanning, versioned images.
- [ ] Zero-downtime migrations, compatible with the previous version.

## 2. Speed
- [ ] Check report and total queries with `EXPLAIN`; indexes on (household, date, category); report caching.
- [ ] Cursor-based expense list instead of offset; full-text search (`pg_trgm`) on the merchant.
- [ ] Connection pooling (PgBouncer); read replicas when needed.
- [ ] Frontend: receipt thumbnails and lazy loading of heavy screens (reports).
- [ ] Asynchronous OCR with result notification and a per-customer cost limit.

## 3. Security and compliance
- [ ] GDPR: export and deletion of user data, record of processing, privacy notice, defined retention.
- [ ] Audit log of actions (sign-ins, changes, exports).
- [ ] Encryption at rest, secret management (not just `.env`), key rotation.
- [ ] Account recovery: password reset by email and address verification.
- [ ] Limits and anti-abuse per plan.
- [ ] External penetration test before launch.

## 4. Features that add value
- [ ] Bank connection (Open Banking: GoCardless, Tink or Plaid): automatic import and reconciliation.
- [ ] Automatic categorisation with rules and suggestions from historical data.
- [ ] Recurring expenses and subscriptions, cash-flow forecasts, savings goals.
- [ ] Attachments and documents with search and warranty tracking.
- [ ] Exports for the accountant and tax returns.
- [ ] Finer roles and permissions (children, read-only guests), invitation by email.
- [ ] Native app or fuller PWA with push notifications.
- [ ] More languages; currencies with historical rates (today rates are manual and undated).

## 5. Monetisation
- [ ] Plans and billing with Stripe (free trial, subscriptions, VAT, invoices).
- [ ] Guided onboarding and sample data.
- [ ] Admin panel for support, limits and suspensions.
- [ ] Public website, terms of use, privacy policy, status page.
- [ ] Self-hosted offering alongside the hosted one (free or licensed edition).
- [ ] Transactional email (verification, summaries) and support.

## Known limitations
- Offline: syncing queued expenses is not idempotent; if the server's response is lost, an expense may be duplicated. Editing and deleting require a connection.
- Backups: two backups on the same day replace each other; full verification of encrypted backups requires the private key inside the container.
