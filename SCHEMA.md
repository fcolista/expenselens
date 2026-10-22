# Schema PostgreSQL

Le migrazioni versionate sono in `backend/migrations/versions/`; le relazioni sono definite in `backend/app/models.py`.

```mermaid
erDiagram
  HOUSEHOLDS ||--o{ USERS : contains
  HOUSEHOLDS ||--o{ CATEGORIES : defines
  HOUSEHOLDS ||--o{ TAGS : defines
  HOUSEHOLDS ||--o{ EXPENSES : owns
  USERS ||--o{ EXPENSES : creates
  CATEGORIES o|--o{ EXPENSES : classifies
  EXPENSES ||--o{ EXPENSE_TAGS : labeled
  TAGS ||--o{ EXPENSE_TAGS : labels
  USERS ||--o{ SESSIONS : authenticates
  HOUSEHOLDS ||--o{ HOUSEHOLD_INVITES : invites
  USERS ||--o{ TELEGRAM_LINK_TOKENS : links
  USERS ||--o| TELEGRAM_USERS : connected
```

| Tabella | Dati principali |
|---|---|
| `households` | Nucleo familiare proprietario dei dati |
| `users` | Email unica, hash Argon2, household, stato MFA e codici di recupero hashati |
| `sessions` | Hash del cookie opaco, scadenza e token CSRF casuale associato alla sessione |
| `categories`, `tags` | Vocabolario separato per household |
| `expenses` | Importo decimale, valuta, data, esercente, descrizione, articoli JSON, visibilità personale/condivisa e riferimento al documento |
| `expense_tags` | Relazione molti-a-molti tra spese e tag |
| `household_invites` | Hash di invito monouso con scadenza |
| `telegram_link_tokens`, `telegram_users` | Collegamento monouso e associazione account Telegram |

I file non sono memorizzati in PostgreSQL: `document_key` punta a un nome casuale nel volume `documents`. Gli export/report leggono i dati via SQL e rispettano household, valuta selezionata e visibilità personale/condivisa.
