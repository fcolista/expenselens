# ExpenseLens

[🇮🇹 Italiano](README.md) · 🇬🇧 English

ExpenseLens is a self-hosted application for tracking the expenses of a small household. The interface is SvelteKit/TypeScript/Tailwind; the API and services are FastAPI/Python 3.12, SQLAlchemy 2, Pydantic 2 and PostgreSQL 16. Documents, database and credentials stay under the control of whoever runs the server. The default OCR is local and free, powered by Tesseract; it needs no account and makes no calls to paid services.

## Running with Docker Compose on Linux and WSL

Install Docker Engine and the Docker Compose plugin, or use Docker Desktop with WSL integration enabled for your Linux distribution. From the Linux shell, go to the project folder (on WSL the path is typically `/mnt/c/...` when the project lives on the Windows C: drive):

```bash
cd /path/to/expenselens
```

Create the local configuration file and generate two random hexadecimal values, which are safe to use in the database URL:

```bash
cp .env.example .env
python3 -c 'import secrets; print(secrets.token_hex(32))'
python3 -c 'import secrets; print(secrets.token_hex(32))'
```

Edit `.env` with a text editor, for example `nano .env`, and replace the example values:

- `POSTGRES_PASSWORD`: the first generated value. Also put it in `DATABASE_URL`, keeping the format `postgresql+psycopg://expenselens:<password>@db:5432/expenselens`.
- `SECRET_KEY`: the second generated value.
- For initial local use, leave `DOMAIN=:80`, `HTTP_PORT=8080`, `HTTPS_PORT=8443`, `FRONTEND_ORIGIN=http://localhost:8080`, `SECURE_COOKIES=false` and `ENVIRONMENT=development`.

Never share `.env` or add it to version control: it contains credentials. Do not use the development HTTP settings on an untrusted network.

Build the images and start the services:

```bash
docker compose up --build -d
docker compose ps
```

Compose starts PostgreSQL, runs the Alembic migrations through the `migrate` service, then starts the API, web interface and proxy. It is normal for `migrate` to exit after applying the migrations. Once `web`, `api` and `proxy` are running, open <http://localhost:8080>. You can check the health endpoint with `curl`:

```bash
curl --fail http://localhost:8080/health
```

The first user creates the household and is the administrator; members added through an invitation have standard access. From the **Family** section the administrator can invite relatives, update their name/email or remove them. When a member is removed, their expenses are kept and attributed to the administrator, documents stay in the volume and the user's sessions are revoked. Categories and tags are managed from the **Categories** section of the dashboard; a tag can be linked to several categories and is offered, together with unlinked tags, when a category is selected for an expense. Individual line amounts accept negative values to represent discounts; the expense total stays positive. After the initial setup, set `ALLOW_REGISTRATION=false` in `.env` and recreate the services with `docker compose up -d`; registration through a valid invitation remains available.

The upgrade assigns the administrator role to the oldest member of every existing household. Apply the migration by restarting the stack:

```bash
docker compose up -d --build
```

Useful commands, always run from the project folder:

```bash
# Follow the logs; Ctrl+C only stops the viewing
docker compose logs -f api proxy

# Restart the services after editing .env
docker compose up -d

# Stop the services, keeping the data
docker compose stop

# Start stopped services again
docker compose start

# Rebuild the images after code changes
docker compose up --build -d
```

Data persists in the Docker volumes `postgres_data` and `documents` even after `docker compose down`. **Do not run `docker compose down -v`** if you want to keep the database and documents: the `-v` option deletes the volumes. Before upgrades or maintenance, take a backup as described in [Backup and restore](#backup-and-restore).

For a server exposed to the Internet:

1. give the server a DNS name;
2. set `DOMAIN=expenses.example.com`, `HTTP_PORT=80`, `HTTPS_PORT=443` and `FRONTEND_ORIGIN=https://expenses.example.com` in `.env`;
3. set `SECURE_COOKIES=true`, `ENVIRONMENT=production`, `ALLOW_REGISTRATION=false`;
4. configure the router/firewall to allow inbound TCP traffic on ports 80 and 443, then run `docker compose up --build -d`.

Caddy handles HTTPS automatically when the DNS name points to the server and the ports are reachable from outside. Never expose the PostgreSQL or FastAPI ports directly. Check with `docker compose ps` and `docker compose logs -f proxy` that the HTTPS certificate was obtained correctly.

## Local development on Linux

The recommended way to develop is Docker Compose, which also starts PostgreSQL and the migrations. To work on a single service you need Python 3.12 and Node 22 installed in the Linux distribution.

From the project root, start PostgreSQL 16:

```bash
docker compose up -d db
```

Then, in the first terminal, start the backend and the migrations:

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
export DATABASE_URL="postgresql+psycopg://expenselens:<password>@localhost:5432/expenselens"
export SECRET_KEY="local-development-secret-change-to-32-characters"
export SECURE_COOKIES=false
export ENVIRONMENT=development
export FRONTEND_ORIGIN=http://localhost:5173
alembic upgrade head
uvicorn app.main:app --reload
```

Replace `<password>` in the URL with the value of `POSTGRES_PASSWORD` from `.env`. The database port is published on loopback only (`127.0.0.1`), not on the network. In a second terminal, from the project root, start the frontend; the Vite proxy forwards `/api` to the local backend.

```bash
cd frontend
npm ci
npm run dev
```

To serve everything from the same origin, use the Compose reverse proxy.

## OCR and vision providers

The default `OCR_PROVIDER=tesseract` uses Tesseract and Poppler inside the backend container, without sending images to external services and at no per-call cost. Italian and English language data are included. The expense description is not filled with the raw receipt text: with structured providers it is built from the detected items, while the merchant name stays in its own field. The payment method is suggested only when the text clearly indicates cash, card or an electronic service; otherwise it is left to be reviewed. Always check the fields on the review screen. The extraction endpoint allows up to 10 OCR uploads per hour per IP address and says explicitly when the limit is reached. The backend image is based on Alpine Linux.

### Azure Document Intelligence

For structured receipt extraction you can use the Azure `prebuilt-receipt` model on the free F0 tier. Microsoft lists 500 free pages per month; F0 limits each document to 4 MB and analyses at most the first two pages. A photo of a receipt counts as one page. Check current quotas and usage in the Azure portal; create an F0 resource and do not move to S0 if you want to stay on the free tier. The image is sent to Azure for analysis. See the [F0 quotas and limits](https://learn.microsoft.com/azure/ai-services/document-intelligence/service-limits) and the documentation of the [prebuilt receipt model](https://learn.microsoft.com/azure/ai-services/document-intelligence/prebuilt/receipt).

In the Azure portal create a **Document Intelligence** resource with the **F0** pricing tier. From the resource page copy the **Endpoint** and one of the keys, then set in `.env`:

```dotenv
OCR_PROVIDER=azure_document_intelligence
AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT=https://<resource>.cognitiveservices.azure.com/
AZURE_DOCUMENT_INTELLIGENCE_KEY=<resource-key>
```

Apply the settings by recreating the API:

```bash
docker compose up -d --force-recreate api
```

### Compatible vision providers

`vision` mode is another external option and may have different costs. For an endpoint compatible with the Chat Completions API with image support, set:

```dotenv
OCR_PROVIDER=vision
VISION_API_URL=https://provider.example/v1/chat/completions
VISION_API_KEY=...
VISION_MODEL=...
```

Keys are read from the environment only. In `vision` or `azure_document_intelligence` mode, the receipt content is sent to the configured provider; check their terms, data location and data handling. Responses are validated with the Pydantic `ReceiptExtraction` schema; the AI does not determine totals or reports and the server does not save the extracted data until the user confirms.

## Telegram

The Telegram bot is an optional channel for **recording and viewing expenses straight from a chat**, without opening the app. It runs as a separate service (`telegram-worker`), uses *long polling* (no webhooks, public domains or open ports needed) and saves expenses in the same database as the app: they appear immediately in lists and reports.

### What it does

- **Receipt photos**: send a photo (or a PDF) in a private chat; the configured OCR extracts amount, merchant and date, and the expense is saved as "to review" with the document attached.
- **Summaries and alerts**: a weekly (Monday) or monthly (day 1) summary at the hour set by `TELEGRAM_NOTIFY_HOUR`, and alerts when a category reaches 80% and 100% of its monthly budget (once per month per level). Enable them with `/notify`.
- **Italian and English**: the language is taken from Telegram when you link the account and can be changed with `/lang`.
- Records an expense in EUR with amount, and optional description, category and date.
- Shows the last 5 expenses, the current month total and the household's categories and tags.
- Respects visibility: you see shared expenses and your own personal ones, never other members' personal expenses.
- Accepts account linking only in a private chat; it sends no unnecessary personal data. Anti-flood: one message every ~1.5 seconds per user.

### Configuration

1. On Telegram open `@BotFather`, send `/newbot`, choose a name and username and copy the token.
2. Put the token in the `.env` file (do not commit it): `TELEGRAM_BOT_TOKEN=123456:ABC...`
3. Start the worker, which belongs to the Compose `telegram` profile:
   ```bash
   docker compose --profile telegram up --build -d telegram-worker
   docker compose logs -f telegram-worker
   ```
   After every token change, recreate the service with the same command. To stop it: `docker compose stop telegram-worker`.
4. In the app open **Settings → Link Telegram**: the single-use command `/start <token>` is copied to the clipboard (if copying is not possible, it is shown on screen).
5. Open your bot on Telegram, in a private chat, and paste the command within 10 minutes. The bot replies "Account linked".

Each ExpenseLens account can be linked to only one Telegram account: a second link from another Telegram account is rejected.

### Commands

| Command | Description |
|---|---|
| `/start TOKEN` | Links the account (private chat only). |
| `/expense 12.50 lunch restaurant` | Records €12.50 with description "lunch restaurant". |
| `/expense 12.50 lunch \| Groceries \| 2026-10-20` | As above, with optional category (exact name) and ISO date. |
| `/expense` | Guided entry: amount, description, date (`-` for today), category (`-` for none). |
| `/latest` | Last 5 visible expenses. |
| `/total` | Current month total in EUR. |
| `/categories`, `/tags` | The household's categories and tags. |
| `/budget` | Status of monthly budgets by category. |
| `/notify` | Sets the summary to `off`/`weekly`/`monthly` and budget alerts (e.g. `/notify weekly`). |
| `/lang` | Changes the bot language: `it` or `en`. |
| `/help` | Summary of the commands. |
| `/cancel` | Cancels the guided entry. |

Every command has an Italian alias (`/spesa`, `/ultime`, `/totale`, `/categorie`, `/tag`, `/notifiche`, `/lingua`, `/aiuto`). Photos require an OCR provider (see above); the service shares the documents volume with the API.

Bot messages are in Italian or English. Expenses entered from Telegram are in EUR, shared with the household, and the merchant equals the description: you can complete them from the app.

## Interface and languages

The interface follows the style of iOS apps and is optimised for touch: a tab bar at the bottom on iPhone and portrait iPad, a sidebar on wide screens (landscape iPad, desktop), large titles, grouped lists, sliding *sheets* for expenses, and respect for safe areas (notch and home indicator). It works in any modern browser and can be installed as an app: on iPhone/iPad use **Share → Add to Home Screen**; the logo (`frontend/static/favicon.svg`, with PNG versions) is used as favicon and app icon.

Language (Italian or English) and theme (auto, light, dark) are chosen in **Settings**, or from the sign-in screen; the default language follows the browser. Translations live in `frontend/src/lib/i18n.ts`: to add a language, add a dictionary.

### App features

- **Passkeys** (Face ID / Touch ID): register the device in **Settings → Passkeys**, then sign in from the login screen. Requires HTTPS (or `localhost`); the domain is derived from `FRONTEND_ORIGIN` or set with `WEBAUTHN_RP_ID`.
- **Active sessions**: list of connected devices, with revocation of one session or all the others.
- **Budgets**: monthly amount per category (in EUR) in the Categories tab, with a progress bar.
- **Currency switch**: quick selector in the expense form. Rates (EUR per 1 unit) are entered manually in Settings (admin only); with `RATES_PROVIDER=frankfurter` they can be refreshed online. Totals and budgets convert currencies that have a rate and ignore those without.
- **Offline (PWA)**: the app is installable and opens without a network; new expenses entered offline are queued and sent when the connection returns. Editing and deleting require a connection. An interrupted send may, rarely, duplicate an expense.
- **Row swipe** (touch): swipe left to duplicate or delete.

## Backup and restore

The Bash scripts save the PostgreSQL dump and the documents together from the Docker volume. Run from the project folder:

```bash
bash scripts/backup.sh
```

Optionally, specify an empty folder for the backup:

```bash
bash scripts/backup.sh ./backups/before-upgrade
```

The script resolves the actual Compose volumes and saves `database.dump`, `documents.tar.gz` and their checksums. The `.env` file (in particular `SECRET_KEY` and `POSTGRES_PASSWORD`) is **not** included in the backup: keep it separately and securely. Keep backups encrypted, off the server and with restricted access.

To restore, pass the backup folder to the script:

```bash
bash scripts/restore.sh ./backups/before-upgrade
```

The restore first verifies the checksums and asks you to type `RIPRISTINA` (the Italian word for "restore"); it then stops the services, replaces the database and documents, and restarts the stack. **Current data will be overwritten.** Test the restore periodically on a separate instance.

### Automatic backups

The `backup` service (a dedicated container, enabled by default) runs every day at `BACKUP_TIME` (default `03:30`, time zone `TZ`, default `Europe/Rome`) a PostgreSQL dump (`pg_dump -Fc`) and an archive of the documents, saved in `./backups/<YYYYmmddTHHMMSSZ>/` together with `SHA256SUMS` and `MANIFEST.json`. The folder is written as `.partial` and renamed only when the backup is complete; a failed backup never deletes previous ones. If no backup newer than 24 hours exists at startup, one is run immediately.

Before the first start, create the folder as your own user (otherwise Docker creates it as root) and, if your UID/GID is not 1000, set `BACKUP_UID`/`BACKUP_GID` in `.env`:

```bash
mkdir -p backups
docker compose up -d --build backup
```

"Grandfather-father-son" rotation: the last backup of each of the last `BACKUP_KEEP_DAILY` days (7), `BACKUP_KEEP_WEEKLY` weeks (4) and `BACKUP_KEEP_MONTHLY` months (6) is kept; the most recent backup is always kept. Only folders created by the agent (timestamp name with `MANIFEST.json`) are deleted: manual folders such as `before-upgrade` are never touched. All variables are documented in `.env.example` ("Automatic backups" section).

Manual commands:

```bash
docker compose exec backup python /app/agent.py backup   # immediate backup
docker compose exec backup python /app/agent.py prune    # apply the rotation
docker compose exec backup python /app/agent.py status   # status (status.json)
docker compose logs -f backup
```

To disable the service set `BACKUP_ENABLED=false` (the agent stays idle).

### Encryption with age

Without `BACKUP_AGE_RECIPIENT` backups are in **plaintext** and the agent warns about it on every run. To encrypt them, generate a key pair (on your own computer, not on the server):

```bash
age-keygen -o age-key.txt          # prints the public key "age1..."
# or, without installing age:
docker run --rm -u "$(id -u)" -v "$PWD:/out" --entrypoint age-keygen ghcr.io/fcolista/expenselens-backup:latest -o /out/age-key.txt
```

Put only the **public** key in `.env` (several keys separated by commas):

```env
BACKUP_AGE_RECIPIENT=age1xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

`database.dump` and `documents.tar.gz` become `database.dump.age` and `documents.tar.gz.age` (encrypted as a stream: plaintext never touches the disk) and checksums are computed on the final files. The **private** key (`age-key.txt`) must be kept off the server, for example in a password manager and an offline copy. **If you lose the private key you lose the backups**: there is no way to recover them. The `.env` file is not part of the backup: keep it separately.

Manual decryption:

```bash
age -d -i age-key.txt backups/<timestamp>/database.dump.age > database.dump
age -d -i age-key.txt backups/<timestamp>/documents.tar.gz.age | tar -tzf -
```

### Remote backup (S3 and WebDAV)

With `BACKUP_REMOTE=s3` or `webdav`, every completed backup is copied (with rclone, configured only through environment variables) to `<destination>/<prefix>/<timestamp>/`. For safety, **the upload is refused if `BACKUP_AGE_RECIPIENT` is empty**, unless `BACKUP_ALLOW_UNENCRYPTED_REMOTE=true` (not recommended). If the upload fails, the local backup remains valid, the status becomes `degraded` and an alert is sent. Remote copies older than `BACKUP_REMOTE_KEEP_DAYS` (30) are deleted, except the most recent one.

AWS S3:

```env
BACKUP_REMOTE=s3
BACKUP_S3_BUCKET=my-bucket
BACKUP_S3_REGION=eu-south-1
BACKUP_S3_ACCESS_KEY=AKIA...
BACKUP_S3_SECRET_KEY=...
BACKUP_S3_PREFIX=expenselens
```

MinIO (or another self-hosted S3):

```env
BACKUP_REMOTE=s3
BACKUP_S3_PROVIDER=Minio
BACKUP_S3_ENDPOINT=https://minio.example.com
BACKUP_S3_BUCKET=expenselens
BACKUP_S3_ACCESS_KEY=...
BACKUP_S3_SECRET_KEY=...
```

Backblaze B2 (S3-compatible API):

```env
BACKUP_REMOTE=s3
BACKUP_S3_PROVIDER=Other
BACKUP_S3_ENDPOINT=https://s3.eu-central-003.backblazeb2.com
BACKUP_S3_REGION=eu-central-003
BACKUP_S3_BUCKET=my-bucket
BACKUP_S3_ACCESS_KEY=<keyID>
BACKUP_S3_SECRET_KEY=<applicationKey>
```

Nextcloud (WebDAV; use an app password):

```env
BACKUP_REMOTE=webdav
BACKUP_WEBDAV_URL=https://cloud.example.com/remote.php/dav/files/user
BACKUP_WEBDAV_USER=user
BACKUP_WEBDAV_PASSWORD=app-password
BACKUP_WEBDAV_VENDOR=nextcloud
BACKUP_WEBDAV_PATH=expenselens
```

### Restore verification

A backup that has never been restored is just a hope. Every week (`BACKUP_VERIFY_DAY=sun`, `BACKUP_VERIFY_TIME=05:00`; `daily` for every day, empty to disable) the agent verifies the latest local backup:

- it checks `SHA256SUMS`;
- with an encrypted backup **and** the private key available, it performs a full restore into a temporary database `expenselens_verify_<timestamp>` (deleted afterwards), with checks on the tables, on `users` and on `alembic_version`, plus `tar -tzf` of the documents;
- with an encrypted backup **without** the private key the verification is only *partial* (checksums and age header); with a plaintext backup the full restore is always performed;
- if a remote is configured, it checks that the remote copy has the same files and sizes.

To enable full verification, mount the private key read-only in the `backup` service (uncomment the line in `docker-compose.yml` or use an override) and set `BACKUP_AGE_IDENTITY_FILE=/run/secrets/age_identity` in `.env`. Note that this puts the private key on the server: it is a trade-off; alternatively keep the partial verification and test the restore elsewhere.

```bash
docker compose exec backup python /app/agent.py verify               # latest backup
docker compose exec backup python /app/agent.py verify --dir /backups/<timestamp>
docker compose exec backup python /app/agent.py status
```

`backups/status.json` contains `last_backup` (`time`, `ok`, `status` = ok/degraded/failed, `dir`, `size_bytes`, `encrypted`, `remote_ok`), `last_verify` (`time`, `result` = ok/partial/failed, `details`), `next_backup` and `warnings`. The container health check (`agent.py health`) fails if the last backup failed or if the last successful one is older than 36 hours; check it with `docker compose ps`.

### Telegram alerts

With `TELEGRAM_BOT_TOKEN` already configured, set `BACKUP_ALERT_TELEGRAM_CHAT_ID` (your chat id) to receive a message on backup, upload or verification errors; with `BACKUP_NOTIFY_SUCCESS=true` also on success. `BACKUP_LANG=it|en` selects the language. Messages contain no secrets.

### Restoring encrypted backups

`scripts/restore.sh` recognises the agent's backups (`.age` files) and requires the private key:

```bash
AGE_IDENTITY_FILE=/safe/path/age-key.txt bash scripts/restore.sh ./backups/<timestamp>
```

Decryption is streamed (with the host's `age` if present, otherwise through the `backup` image, with the key mounted read-only) and plaintext is never written to disk. Checksums and the `RIPRISTINA` confirmation remain mandatory, and the key is verified before any data is changed. For backups downloaded from a remote, copy the whole `<timestamp>` folder into `./backups/` or pass its path.

### Troubleshooting

- `cannot write to /backups`: the `./backups` folder does not belong to `BACKUP_UID`/`BACKUP_GID` (e.g. created by Docker as root). Run `sudo chown "$(id -u):$(id -g)" backups` and set both values in `.env`.
- `upload refused: BACKUP_AGE_RECIPIENT is empty`: set the age public key, or (not recommended) `BACKUP_ALLOW_UNENCRYPTED_REMOTE=true`.
- Remote upload failed: check endpoint, bucket/path and credentials with `docker compose logs backup`; secrets are never written to the logs.
- `partial` verification: the private key is missing (`BACKUP_AGE_IDENTITY_FILE`); `failed` verification: read `last_verify.details` in `status.json`.
- Container `unhealthy`: `docker compose exec backup python /app/agent.py health` shows the reason; then run `... backup` to retry.
- PostgreSQL version: the image uses `postgresql16-client`, to be updated in `backup/Dockerfile` if you change the database's major version.

## Releases, images and upgrades

The `VERSION` file (e.g. `1.0.0`) is the single source of truth for the version. On every push to `main`, GitHub Actions (`.github/workflows/release.yml`) runs backend tests, frontend checks and Compose validation. If `VERSION` does not yet have the tag `vX.Y.Z`, it builds and publishes the three images to GHCR with **the same tag**, for `linux/amd64` and `linux/arm64`, then creates the git tag and the GitHub release:

- `ghcr.io/<owner>/expenselens-backend` (used by `api`, `migrate` and `telegram-worker`)
- `ghcr.io/<owner>/expenselens-web`
- `ghcr.io/<owner>/expenselens-backup`

To publish a new version: edit `VERSION`, commit and push to `main`. Nothing is published unless `VERSION` changes. Images also get the `latest` tag.

In production set `EXPENSELENS_REGISTRY` (default `ghcr.io/fcolista`) and `EXPENSELENS_VERSION` (a fixed number such as `1.0.0` is recommended) in `.env`, then:

```bash
docker compose pull
docker compose up -d
```

The database migration runs automatically at startup. Take a backup before every upgrade. If the GHCR package is private, run `docker login ghcr.io` first; for local development, `docker compose build` builds the images from the code.

## Tests and checks

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
pytest
pip-audit
deactivate
cd ../frontend
npm ci
npm run check
npm run build
cd ..
trivy config .
trivy fs .
```

`pip-audit` checks the Python dependencies; Trivy checks configurations and the filesystem. Run these checks and update images/dependencies before a release. To check the Docker images, install Trivy and also use `trivy image` on the built images.

## Security notes and limits

- Sessions are opaque random tokens stored in the database as hashes; the random CSRF token is tied to the session. Cookies are `HttpOnly`, `SameSite=Lax`, with an expiry; `Secure` is configurable and must stay on over HTTPS. Mutations also require the CSRF token.
- Passwords are hashed with Argon2; TOTP MFA and single-use recovery codes can be configured from **Settings → Security**.
- Registration, login, upload and API calls are rate limited. Documents have random server-side names and are served only to those who can read the linked expense.
- Queries and reports are limited to the household and totals are computed by the database for the selected currency. Per-currency totals are not converted; only the Expenses screen and budgets use the optional rates you set.
- To keep backups consistent, save the database and the documents volume together.
- Administrative member management does not include password resets or revoking invitations already issued; each member manages their own credentials.
