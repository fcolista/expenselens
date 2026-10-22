# ExpenseLens

🇮🇹 Italiano · [🇬🇧 English](README.en.md)

ExpenseLens è un'applicazione self-hosted per registrare le spese di un piccolo nucleo familiare. L'interfaccia è SvelteKit/TypeScript/Tailwind; API e servizi sono FastAPI/Python 3.12, SQLAlchemy 2, Pydantic 2 e PostgreSQL 16. Documenti, database e credenziali restano sotto il controllo di chi gestisce il server. L'OCR predefinito è locale e gratuito, eseguito con Tesseract; non richiede account o chiamate a servizi a pagamento.

## Avvio con Docker Compose su Linux e WSL

Installa Docker Engine e il plugin Docker Compose, oppure usa Docker Desktop con l'integrazione WSL attiva per la distribuzione Linux. Dalla shell Linux, raggiungi la cartella del progetto (il percorso `/mnt/c/...` è quello tipico quando il progetto si trova sul disco C: di Windows):

```bash
cd /path/to/expenselens
```

Crea il file di configurazione locale e genera due valori casuali esadecimali, compatibili con l'URL del database:

```bash
cp .env.example .env
python3 -c 'import secrets; print(secrets.token_hex(32))'
python3 -c 'import secrets; print(secrets.token_hex(32))'
```

Modifica `.env` con un editor di testo, ad esempio `nano .env`, e sostituisci i valori di esempio:

- `POSTGRES_PASSWORD`: il primo valore generato. Inseriscilo anche in `DATABASE_URL`, mantenendo il formato `postgresql+psycopg://expenselens:<password>@db:5432/expenselens`.
- `SECRET_KEY`: il secondo valore generato.
- Per l'uso iniziale in locale lascia `DOMAIN=:80`, `HTTP_PORT=8080`, `HTTPS_PORT=8443`, `FRONTEND_ORIGIN=http://localhost:8080`, `SECURE_COOKIES=false` ed `ENVIRONMENT=development`.

Non condividere né aggiungere `.env` al controllo versione: contiene credenziali. Non usare le impostazioni HTTP di sviluppo su una rete non fidata.

Costruisci le immagini e avvia i servizi:

```bash
docker compose up --build -d
docker compose ps
```

Compose avvia PostgreSQL, esegue le migrazioni Alembic tramite il servizio `migrate`, quindi avvia API, interfaccia web e proxy. È normale che `migrate` termini dopo aver applicato le migrazioni. Quando `web`, `api` e `proxy` risultano avviati, apri <http://localhost:8080>. Puoi controllare l'endpoint di stato con `curl`:

```bash
curl --fail http://localhost:8080/health
```

Il primo utente crea il nucleo familiare ed è amministratore; i membri aggiunti tramite invito hanno accesso standard. Dalla sezione **Famiglia** l'amministratore può invitare familiari, aggiornare nome/email o rimuoverli. Alla rimozione le spese vengono mantenute e attribuite all'amministratore, i documenti restano nel volume e le sessioni dell'utente vengono revocate. Categorie e tag si gestiscono dalla sezione **Categorie** della dashboard; un tag può essere collegato a più categorie e viene proposto insieme ai tag non associati quando si seleziona una categoria per una spesa. Gli importi delle singole righe accettano valori negativi per rappresentare sconti; il totale della spesa rimane positivo. Dopo la configurazione iniziale, imposta `ALLOW_REGISTRATION=false` in `.env` e ricrea i servizi con `docker compose up -d`; la registrazione tramite invito valido resta disponibile.

L'aggiornamento assegna il ruolo amministratore al membro più vecchio di ogni famiglia esistente. Applica la migrazione riavviando lo stack:

```bash
docker compose up -d --build
```

Comandi utili, da eseguire sempre nella cartella `ExpenseLens`:

```bash
# Segui i log; Ctrl+C interrompe solo la visualizzazione
docker compose logs -f api proxy

# Riavvia i servizi dopo aver modificato .env
docker compose up -d

# Arresta i servizi mantenendo i dati
docker compose stop

# Riavvia i servizi arrestati
docker compose start

# Ricostruisci le immagini dopo modifiche al codice
docker compose up --build -d
```

I dati persistono nei volumi Docker `postgres_data` e `documents` anche dopo `docker compose down`. **Non eseguire `docker compose down -v`** se vuoi conservare database e documenti: l'opzione `-v` elimina i volumi. Prima di aggiornamenti o manutenzione, fai il backup descritto nella sezione [Backup e ripristino](#backup-e-ripristino).

Per un server esposto su Internet:

1. assegna un nome DNS al server;
2. imposta `DOMAIN=spese.example.it`, `HTTP_PORT=80`, `HTTPS_PORT=443` e `FRONTEND_ORIGIN=https://spese.example.it` in `.env`;
3. imposta `SECURE_COOKIES=true`, `ENVIRONMENT=production`, `ALLOW_REGISTRATION=false`;
4. configura il router/firewall per consentire il traffico TCP in ingresso sulle porte 80 e 443 e avvia `docker compose up --build -d`.

Caddy gestisce automaticamente HTTPS quando il nome DNS punta al server e le porte sono raggiungibili dall'esterno. Non esporre direttamente le porte di PostgreSQL o FastAPI. Verifica con `docker compose ps` e `docker compose logs -f proxy` che il certificato HTTPS sia stato ottenuto correttamente.

## Sviluppo locale su Linux

La strada raccomandata per lo sviluppo è Docker Compose, che avvia anche PostgreSQL e le migrazioni. Per lavorare su un solo servizio servono Python 3.12 e Node 22 installati nella distribuzione Linux.

Dal terminale nella cartella principale del progetto, avvia PostgreSQL 16:

```bash
docker compose up -d db
```

Quindi, nel primo terminale, avvia il backend e le migrazioni:

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

Sostituisci `<password>` nell'URL con il valore di `POSTGRES_PASSWORD` in `.env`. La porta del database è pubblicata solo su loopback (`127.0.0.1`), non sulla rete. In un secondo terminale, dalla cartella principale del progetto, avvia il frontend; il proxy Vite inoltra `/api` al backend locale.

```bash
cd frontend
npm ci
npm run dev
```

Per servire tutto dallo stesso origin usa il reverse proxy Compose.

## OCR e provider vision

Il valore predefinito `OCR_PROVIDER=tesseract` usa Tesseract e Poppler nel container backend, senza inviare immagini a servizi esterni e senza costi per chiamata. Sono inclusi i dati linguistici italiani e inglesi. La descrizione della spesa non viene riempita con il testo grezzo dello scontrino: con i provider strutturati è composta dagli articoli rilevati, mentre il nome dell'esercente resta nel proprio campo. Il metodo di pagamento viene suggerito solo quando il testo indica chiaramente contanti, carta o un servizio elettronico; altrimenti resta da verificare. Controlla sempre i campi nella schermata di revisione. L'endpoint di estrazione consente fino a 10 caricamenti OCR all'ora per indirizzo IP e comunica esplicitamente quando il limite è raggiunto. L'immagine backend è basata su Alpine Linux.

### Azure Document Intelligence

Per l'estrazione strutturata degli scontrini puoi usare il modello Azure `prebuilt-receipt` con il piano gratuito F0. Microsoft indica 500 pagine gratuite al mese; F0 limita ogni documento a 4 MB e analizza al massimo le prime due pagine. Una foto di uno scontrino conta una pagina. Controlla quote e utilizzo correnti nel portale Azure; crea una risorsa F0 e non passare al piano S0 se vuoi restare sul piano gratuito. L'immagine viene inviata ad Azure per l'analisi. Consulta le [quote e i limiti F0](https://learn.microsoft.com/azure/ai-services/document-intelligence/service-limits) e la documentazione del [modello predefinito per scontrini](https://learn.microsoft.com/azure/ai-services/document-intelligence/prebuilt/receipt).

Nel portale Azure crea una risorsa **Document Intelligence** con piano tariffario **F0**. Dalla pagina della risorsa copia **Endpoint** e una delle chiavi, quindi imposta in `.env`:

```dotenv
OCR_PROVIDER=azure_document_intelligence
AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT=https://<risorsa>.cognitiveservices.azure.com/
AZURE_DOCUMENT_INTELLIGENCE_KEY=<chiave-della-risorsa>
```

Applica le impostazioni ricreando l'API:

```bash
docker compose up -d --force-recreate api
```

L'app usa l'API REST 2024-11-30 e il modello predefinito per scontrini, mappando esercente, data, totale, imposta e articoli. L'estrazione Azure è asincrona; se il documento supera i limiti F0 o la quota mensile è esaurita, l'errore viene mostrato e non si passa silenziosamente a un altro servizio. Per tornare all'OCR interamente locale, imposta `OCR_PROVIDER=tesseract` e ricrea l'API.

### Provider vision compatibili

La modalità `vision` è un'altra opzione esterna e potrebbe avere costi diversi. Per un endpoint compatibile con l'API Chat Completions con supporto immagini, imposta:

```dotenv
OCR_PROVIDER=vision
VISION_API_URL=https://provider.example/v1/chat/completions
VISION_API_KEY=...
VISION_MODEL=...
```

Le chiavi sono lette esclusivamente dall'ambiente. In modalità `vision` o `azure_document_intelligence`, il contenuto delle ricevute viene inviato al provider configurato; verifica condizioni, ubicazione e trattamento dei dati. Le risposte sono validate con lo schema Pydantic `ReceiptExtraction`; l'AI non determina totali o report e il server non salva i dati estratti fino alla conferma dell'utente.

## Telegram

Il bot Telegram è un canale opzionale per **registrare e consultare le spese direttamente da chat**, senza aprire l'app. Gira come servizio separato (`telegram-worker`), usa il *long polling* (non servono webhook, domini pubblici o porte aperte) e salva le spese nello stesso database dell'app: compaiono subito in elenco e nei report.

### Cosa fa

- **Foto dello scontrino**: invia una foto (o un PDF) in chat privata; l'OCR configurato ne estrae importo, esercente e data e la spesa viene salvata «da rivedere» con il documento allegato.
- **Riepiloghi e avvisi**: riepilogo settimanale (lunedì) o mensile (giorno 1) all'ora `TELEGRAM_NOTIFY_HOUR`, e avvisi quando una categoria raggiunge l'80% e il 100% del budget mensile (una volta al mese per livello). Si attivano con `/notifiche`.
- **Italiano e inglese**: la lingua è presa da Telegram al collegamento e si cambia con `/lingua`.
- Registra una spesa in EUR con importo, descrizione, categoria e data facoltative.
- Mostra le ultime 5 spese, il totale del mese corrente e l'elenco di categorie e tag della famiglia.
- Rispetta la visibilità: vede le spese condivise e le proprie spese personali, mai quelle personali degli altri familiari.
- Accetta il collegamento dell'account solo in chat privata; non invia dati personali non necessari. Anti-flood: un messaggio ogni ~1,5 secondi per utente.

### Configurazione

1. Su Telegram apri `@BotFather`, invia `/newbot`, scegli nome e username e copia il token.
2. Inserisci il token nel file `.env` (non committarlo): `TELEGRAM_BOT_TOKEN=123456:ABC...`
3. Avvia il worker, che fa parte del profilo `telegram` di Compose:
   ```bash
   docker compose --profile telegram up --build -d telegram-worker
   docker compose logs -f telegram-worker
   ```
   Dopo ogni modifica del token ricrea il servizio con lo stesso comando. Per fermarlo: `docker compose stop telegram-worker`.
4. Nell'app apri **Impostazioni → Collega Telegram**: il comando monouso `/start <token>` viene copiato negli appunti (se la copia non è possibile, viene mostrato a schermo).
5. Apri il tuo bot su Telegram, in privato, e incolla il comando entro 10 minuti. Il bot risponde «Account collegato».

Ogni account ExpenseLens può essere collegato a un solo account Telegram: un secondo collegamento da un altro account Telegram viene rifiutato.

### Comandi

| Comando | Descrizione |
|---|---|
| `/start TOKEN` | Collega l'account (solo chat privata). |
| `/spesa 12,50 pranzo ristorante` | Registra 12,50 € con descrizione «pranzo ristorante». |
| `/spesa 12,50 pranzo \| Alimentari \| 2026-10-20` | Come sopra, con categoria (nome esatto) e data ISO facoltative. |
| `/spesa` | Inserimento guidato: importo, descrizione, data (`-` per oggi), categoria (`-` per nessuna). |
| `/ultime` | Ultime 5 spese visibili. |
| `/totale` | Totale in EUR del mese corrente. |
| `/categorie`, `/tag` | Elenco di categorie e tag della famiglia. |
| `/budget` | Stato dei budget mensili per categoria. |
| `/notifiche` (`/notify`) | Imposta riepilogo `off`/`weekly`/`monthly` e avvisi budget (es. `/notifiche weekly`). |
| `/lingua` (`/lang`) | Cambia lingua del bot: `it` o `en`. |
| `/aiuto` | Riepilogo dei comandi. |
| `/cancel` | Annulla l'inserimento guidato. |

Ogni comando ha l'alias inglese (`/expense`, `/latest`, `/total`, `/categories`, `/tags`, `/help`). Le foto richiedono un provider OCR (vedi sopra); il servizio condivide il volume dei documenti con l'API.

I messaggi del bot sono in italiano o inglese. Le spese inserite da Telegram hanno valuta EUR, sono condivise con la famiglia e l'esercente coincide con la descrizione: puoi completarle dall'app.

## Interfaccia e lingue

L'interfaccia segue lo stile delle app iOS ed è ottimizzata per touch: barra delle schede in basso su iPhone e iPad in verticale, barra laterale su schermi larghi (iPad in orizzontale, desktop), titoli grandi, liste raggruppate, *sheet* a scorrimento per le spese, rispetto delle aree sicure (notch e home indicator). Funziona su qualsiasi browser moderno ed è installabile come app: su iPhone/iPad usa **Condividi → Aggiungi a Home**; il logo (`frontend/static/favicon.svg`, con le versioni PNG) è usato come favicon e icona dell'app.

La lingua (italiano o inglese) e il tema (auto, chiaro, scuro) si scelgono in **Impostazioni**, o dalla schermata di accesso; la lingua predefinita segue quella del browser. Le traduzioni sono in `frontend/src/lib/i18n.ts`: per aggiungere una lingua basta aggiungere un dizionario.

### Funzioni dell'app

- **Passkey** (Face ID / Touch ID): in **Impostazioni → Passkey** registra il dispositivo, poi accedi dalla schermata di login. Richiede HTTPS (o `localhost`); il dominio è dedotto da `FRONTEND_ORIGIN` oppure impostato con `WEBAUTHN_RP_ID`.
- **Cambio password**: da Impostazioni, con la password attuale (e il codice MFA se attivo). Le altre sessioni vengono disconnesse; non c'è ancora un recupero della password dimenticata (vedi `TODO.md`).
- **Sessioni attive**: elenco dei dispositivi collegati, con revoca singola o di tutte le altre.
- **Budget**: importo mensile per categoria (in EUR) nella scheda Categorie, con barra di avanzamento.
- **Cambio valuta**: selettore rapido nel form della spesa. I tassi (EUR per 1 unità) si inseriscono a mano in Impostazioni (solo admin); con `RATES_PROVIDER=frankfurter` si può aggiornarli online. I totali e i budget convertono le valute con tasso e ignorano quelle senza.
- **Offline (PWA)**: l'app è installabile e si apre senza rete; le nuove spese inserite offline sono messe in coda e inviate al ritorno della connessione. Modifica ed eliminazione richiedono rete. Un invio interrotto potrebbe, raramente, duplicare una spesa.
- **Swipe sulle righe** (touch): scorri a sinistra per duplicare o eliminare.

## Backup e ripristino

Gli script Bash salvano insieme dump PostgreSQL e documenti nel volume Docker. Esegui dalla cartella del progetto:

```bash
bash scripts/backup.sh
```

Facoltativamente, specifica una cartella vuota per il backup:

```bash
bash scripts/backup.sh ./backups/prima-aggiornamento
```

Lo script risolve i volumi Compose effettivi, salva `database.dump`, `documents.tar.gz` e i relativi checksum. Il file `.env` (in particolare `SECRET_KEY` e `POSTGRES_PASSWORD`) **non** è incluso nel backup: conservalo separatamente e in modo sicuro. Conserva i backup cifrati, fuori dal server e con accesso ristretto.

Per ripristinare, passa la cartella del backup allo script:

```bash
bash scripts/restore.sh ./backups/prima-aggiornamento
```

Il ripristino verifica prima i checksum e richiede di digitare `RIPRISTINA`; poi ferma i servizi, sostituisce database e documenti, e riavvia lo stack. **I dati correnti verranno sovrascritti.** Prova periodicamente il ripristino su un'istanza separata.

### Backup automatici

Il servizio `backup` (container dedicato, attivo di default) esegue ogni giorno alle `BACKUP_TIME` (default `03:30`, fuso orario `TZ`, default `Europe/Rome`) un dump PostgreSQL (`pg_dump -Fc`) e un archivio dei documenti, salvati in `./backups/<YYYYmmddTHHMMSSZ>/` insieme a `SHA256SUMS` e `MANIFEST.json`. La cartella viene scritta come `.partial` e rinominata solo a backup completo; un backup fallito non cancella mai quelli precedenti. Se all'avvio non esiste un backup più recente di 24 ore, ne viene eseguito uno subito.

Prima del primo avvio crea la cartella con il tuo utente (altrimenti Docker la crea come root) e, se il tuo UID/GID non è 1000, imposta `BACKUP_UID`/`BACKUP_GID` nel `.env`:

```bash
mkdir -p backups
docker compose up -d --build backup
```

Rotazione "nonno-padre-figlio": si conservano l'ultimo backup di ognuno degli ultimi `BACKUP_KEEP_DAILY` giorni (7), `BACKUP_KEEP_WEEKLY` settimane (4) e `BACKUP_KEEP_MONTHLY` mesi (6); il backup più recente è sempre conservato. Vengono eliminate solo le cartelle create dall'agente (nome a timestamp con `MANIFEST.json`): cartelle manuali come `prima-aggiornamento` non vengono mai toccate. Tutte le variabili sono documentate in `.env.example` (sezione "Automatic backups").

Comandi manuali:

```bash
docker compose exec backup python /app/agent.py backup   # backup immediato
docker compose exec backup python /app/agent.py prune    # applica la rotazione
docker compose exec backup python /app/agent.py status   # stato (status.json)
docker compose logs -f backup
```

Per disattivare il servizio imposta `BACKUP_ENABLED=false` (l'agente resta inattivo).

### Cifratura con age

Senza `BACKUP_AGE_RECIPIENT` i backup sono in **chiaro** e l'agente lo segnala a ogni esecuzione. Per cifrarli genera una coppia di chiavi (sul tuo computer, non sul server):

```bash
age-keygen -o age-key.txt          # stampa la chiave pubblica "age1..."
# oppure, senza installare age:
docker run --rm -u "$(id -u)" -v "$PWD:/out" --entrypoint age-keygen ghcr.io/fcolista/expenselens-backup:latest -o /out/age-key.txt
```

Metti nel `.env` solo la chiave **pubblica** (più chiavi separate da virgola):

```env
BACKUP_AGE_RECIPIENT=age1xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

`database.dump` e `documents.tar.gz` diventano `database.dump.age` e `documents.tar.gz.age` (cifrati in streaming: il testo in chiaro non tocca mai il disco) e i checksum sono calcolati sui file finali. La chiave **privata** (`age-key.txt`) va conservata fuori dal server, ad esempio in un password manager e in una copia offline. **Se perdi la chiave privata perdi i backup**: non esiste alcun modo di recuperarli. Il file `.env` non fa parte del backup: conservalo a parte.

Decifratura manuale:

```bash
age -d -i age-key.txt backups/<timestamp>/database.dump.age > database.dump
age -d -i age-key.txt backups/<timestamp>/documents.tar.gz.age | tar -tzf -
```

### Backup remoto (S3 e WebDAV)

Con `BACKUP_REMOTE=s3` o `webdav` ogni backup completato viene copiato (con rclone, configurato solo da variabili d'ambiente) in `<destinazione>/<prefisso>/<timestamp>/`. Per sicurezza **l'upload è rifiutato se `BACKUP_AGE_RECIPIENT` è vuoto**, salvo `BACKUP_ALLOW_UNENCRYPTED_REMOTE=true` (sconsigliato). Se l'upload fallisce il backup locale resta valido, lo stato diventa `degraded` e arriva un avviso. Le copie remote più vecchie di `BACKUP_REMOTE_KEEP_DAYS` (30) vengono eliminate, tranne la più recente.

AWS S3:

```env
BACKUP_REMOTE=s3
BACKUP_S3_BUCKET=il-mio-bucket
BACKUP_S3_REGION=eu-south-1
BACKUP_S3_ACCESS_KEY=AKIA...
BACKUP_S3_SECRET_KEY=...
BACKUP_S3_PREFIX=expenselens
```

MinIO (o altro S3 self-hosted):

```env
BACKUP_REMOTE=s3
BACKUP_S3_PROVIDER=Minio
BACKUP_S3_ENDPOINT=https://minio.example.com
BACKUP_S3_BUCKET=expenselens
BACKUP_S3_ACCESS_KEY=...
BACKUP_S3_SECRET_KEY=...
```

Backblaze B2 (API compatibile S3):

```env
BACKUP_REMOTE=s3
BACKUP_S3_PROVIDER=Other
BACKUP_S3_ENDPOINT=https://s3.eu-central-003.backblazeb2.com
BACKUP_S3_REGION=eu-central-003
BACKUP_S3_BUCKET=il-mio-bucket
BACKUP_S3_ACCESS_KEY=<keyID>
BACKUP_S3_SECRET_KEY=<applicationKey>
```

Nextcloud (WebDAV; usa una password per app):

```env
BACKUP_REMOTE=webdav
BACKUP_WEBDAV_URL=https://cloud.example.com/remote.php/dav/files/utente
BACKUP_WEBDAV_USER=utente
BACKUP_WEBDAV_PASSWORD=password-per-app
BACKUP_WEBDAV_VENDOR=nextcloud
BACKUP_WEBDAV_PATH=expenselens
```

### Verifica del ripristino

Un backup mai ripristinato è solo una speranza. Ogni settimana (`BACKUP_VERIFY_DAY=sun`, `BACKUP_VERIFY_TIME=05:00`; `daily` per ogni giorno, vuoto per disattivare) l'agente verifica l'ultimo backup locale:

- controlla `SHA256SUMS`;
- con backup cifrato **e** chiave privata disponibile esegue un ripristino completo in un database temporaneo `expenselens_verify_<timestamp>` (che viene poi eliminato), con controlli sulle tabelle, su `users` e su `alembic_version`, più `tar -tzf` dei documenti;
- con backup cifrato **senza** chiave privata la verifica è solo *parziale* (checksum e intestazione age); con backup in chiaro il ripristino completo è sempre eseguito;
- se è configurato un remoto, controlla che la copia remota abbia gli stessi file e dimensioni.

Per abilitare la verifica completa monta la chiave privata in sola lettura nel servizio `backup` (decommenta la riga nel `docker-compose.yml` o usa un override) e imposta nel `.env` `BACKUP_AGE_IDENTITY_FILE=/run/secrets/age_identity`. Tieni presente che così la chiave privata risiede sul server: è un compromesso, in alternativa lascia la verifica parziale e prova il ripristino altrove.

```bash
docker compose exec backup python /app/agent.py verify               # ultimo backup
docker compose exec backup python /app/agent.py verify --dir /backups/<timestamp>
docker compose exec backup python /app/agent.py status
```

`backups/status.json` contiene `last_backup` (`time`, `ok`, `status` = ok/degraded/failed, `dir`, `size_bytes`, `encrypted`, `remote_ok`), `last_verify` (`time`, `result` = ok/partial/failed, `details`), `next_backup` e `warnings`. Il health check del container (`agent.py health`) fallisce se l'ultimo backup è fallito o se l'ultimo riuscito ha più di 36 ore; controllalo con `docker compose ps`.

### Avvisi Telegram

Con `TELEGRAM_BOT_TOKEN` già configurato, imposta `BACKUP_ALERT_TELEGRAM_CHAT_ID` (il tuo chat id) per ricevere un messaggio in caso di errore di backup, upload o verifica; con `BACKUP_NOTIFY_SUCCESS=true` anche in caso di successo. `BACKUP_LANG=it|en` sceglie la lingua. I messaggi non contengono segreti.

### Ripristino di backup cifrati

`scripts/restore.sh` riconosce i backup dell'agente (file `.age`) e richiede la chiave privata:

```bash
AGE_IDENTITY_FILE=/percorso/sicuro/age-key.txt bash scripts/restore.sh ./backups/<timestamp>
```

La decifratura avviene in streaming (con `age` dell'host se presente, altrimenti tramite l'immagine `backup`, con la chiave montata in sola lettura) e il testo in chiaro non viene scritto su disco. Checksum e conferma `RIPRISTINA` restano obbligatori, e la chiave viene verificata prima di modificare qualsiasi dato. Per i backup scaricati da un remoto, copia l'intera cartella `<timestamp>` in `./backups/` oppure passane il percorso.

### Risoluzione dei problemi

- `cannot write to /backups`: la cartella `./backups` non appartiene a `BACKUP_UID`/`BACKUP_GID` (es. creata da Docker come root). Esegui `sudo chown "$(id -u):$(id -g)" backups` e imposta i due valori nel `.env`.
- `upload refused: BACKUP_AGE_RECIPIENT is empty`: imposta la chiave pubblica age, oppure (sconsigliato) `BACKUP_ALLOW_UNENCRYPTED_REMOTE=true`.
- Upload remoto fallito: controlla endpoint, bucket/percorso e credenziali con `docker compose logs backup`; i segreti non vengono mai scritti nei log.
- Verifica `partial`: manca la chiave privata (`BACKUP_AGE_IDENTITY_FILE`); verifica `failed`: leggi `last_verify.details` in `status.json`.
- Container `unhealthy`: `docker compose exec backup python /app/agent.py health` mostra il motivo; poi `... backup` per riprovare.
- Versione di PostgreSQL: l'immagine usa `postgresql16-client`, da aggiornare in `backup/Dockerfile` se cambi la versione maggiore del database.

## Release, immagini e aggiornamenti

Il file `VERSION` (es. `1.0.0`) è l'unica fonte della versione. A ogni push su `main`, GitHub Actions (`.github/workflows/release.yml`) esegue test backend, controlli frontend e validazione di Compose. Se `VERSION` non ha ancora il tag `vX.Y.Z`, costruisce e pubblica su GHCR le tre immagini con **lo stesso tag**, per `linux/amd64` e `linux/arm64`, poi crea il tag git e la release GitHub:

- `ghcr.io/<proprietario>/expenselens-backend` (usata da `api`, `migrate` e `telegram-worker`)
- `ghcr.io/<proprietario>/expenselens-web`
- `ghcr.io/<proprietario>/expenselens-backup`

Per pubblicare una nuova versione: modifica `VERSION`, fai commit e push su `main`. Senza modificare `VERSION` non viene pubblicato nulla. Le immagini hanno anche il tag `latest`.

In produzione imposta in `.env` `EXPENSELENS_REGISTRY` (predefinito `ghcr.io/fcolista`) e `EXPENSELENS_VERSION` (consigliato un numero fisso, es. `1.0.0`), poi:

```bash
docker compose pull
docker compose up -d
```

La migrazione del database parte da sola all'avvio. Fai un backup prima di ogni aggiornamento. Se il pacchetto GHCR è privato, esegui prima `docker login ghcr.io`; per sviluppo locale `docker compose build` costruisce le immagini dal codice.

## Test e controlli

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

`pip-audit` controlla le dipendenze Python; Trivy controlla configurazioni e filesystem. Esegui questi controlli e aggiorna le immagini/dipendenze prima di una pubblicazione. Per una verifica delle immagini Docker installa Trivy e usa anche `trivy image` sulle immagini costruite.

## Note di sicurezza e limiti

- Le sessioni sono token casuali opachi conservati nel database come hash; il token CSRF casuale è associato alla sessione. I cookie sono `HttpOnly`, `SameSite=Lax`, con scadenza; `Secure` è configurabile e va mantenuto attivo su HTTPS. Le mutazioni richiedono inoltre il token CSRF.
- Le password sono hashate con Argon2; MFA TOTP e codici di recupero monouso sono configurabili da **Impostazioni → Sicurezza**.
- Registrazione, login, upload e API hanno limiti di frequenza. I documenti hanno nomi casuali server-side e sono serviti solo a chi può leggere la spesa collegata.
- Le query e i report sono limitati all'household e i totali sono calcolati dal database per la valuta selezionata. I totali per valuta non sono convertiti; solo la schermata Spese e i budget usano i tassi opzionali impostati.
- Per mantenere i backup coerenti, salva insieme il database e il volume documenti.
- La gestione amministrativa dei membri non include reset password o revoca degli inviti già emessi; ogni membro gestisce le proprie credenziali.
