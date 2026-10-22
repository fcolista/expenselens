# TODO

Idee per le prossime versioni di ExpenseLens, in ordine di priorità consigliato. Sono
priorità da verificare: non sono state misurate prestazioni o carichi.

## Ordine consigliato
1. Test automatici e CI (frontend ed end-to-end), log strutturati, Sentry, backup già verificati.
2. Reset password, verifica email e funzioni GDPR.
3. Redis e worker per i lavori pesanti, storage S3, test di carico (k6).
4. Piani e fatturazione con Stripe, poi pannello admin.
5. Collegamento bancario e categorizzazione automatica.

## 1. Base per vendere: multi-tenant e affidabilità
- [ ] Isolamento tra clienti: test automatici contro fughe di dati tra household e, su Postgres, row-level security.
- [ ] Applicazione senza stato: Redis per limiti di richieste, cache e code; storage S3 condiviso per i documenti.
- [ ] Code per i lavori pesanti (OCR, notifiche, riepiloghi, backup) con un worker dedicato (arq o Celery).
- [ ] Osservabilità: log strutturati, metriche Prometheus/Grafana, Sentry, controlli di salute e allarmi.
- [ ] CI/CD: test frontend e end-to-end (Playwright), scansione delle dipendenze, immagini versionate.
- [ ] Migrazioni senza fermo, compatibili con la versione precedente.

## 2. Velocità
- [ ] Controllare con `EXPLAIN` le query di report e totali; indici su (household, data, categoria); cache dei report.
- [ ] Elenco spese con cursore invece di offset; ricerca full-text (`pg_trgm`) sull'esercente.
- [ ] Connection pool (PgBouncer); repliche in lettura quando serve.
- [ ] Frontend: anteprime ridotte degli scontrini e caricamento a richiesta delle schermate pesanti (report).
- [ ] OCR asincrono con notifica del risultato e limite di costo per cliente.

## 3. Sicurezza e conformità
- [ ] GDPR: esportazione e cancellazione dei dati dell'utente, registro dei trattamenti, informativa, conservazione definita.
- [ ] Audit log delle azioni (accessi, modifiche, esportazioni).
- [ ] Cifratura dei dati a riposo, gestione dei segreti (non solo `.env`), rotazione delle chiavi.
- [ ] Recupero account: reset password via email e verifica dell'indirizzo.
- [ ] Limiti e anti-abuso per piano.
- [ ] Penetration test esterno prima del lancio.

## 4. Funzioni che portano valore
- [ ] Collegamento bancario (Open Banking: GoCardless, Tink o Plaid): importazione automatica e riconciliazione.
- [ ] Categorizzazione automatica con regole e suggerimenti dai dati storici.
- [ ] Spese ricorrenti e abbonamenti, previsioni di cassa, obiettivi di risparmio.
- [ ] Allegati e documenti con ricerca e conservazione delle garanzie.
- [ ] Esportazioni per commercialista e dichiarazione dei redditi.
- [ ] Ruoli e permessi più fini (figli, ospiti in sola lettura), invito via email.
- [ ] App nativa o PWA più completa con notifiche push.
- [ ] Altre lingue; valute con tassi storici (oggi i tassi sono manuali e senza data).

## 5. Per monetizzare
- [ ] Piani e fatturazione con Stripe (prova gratuita, abbonamenti, IVA, fatture).
- [ ] Onboarding guidato e dati di esempio.
- [ ] Pannello amministrativo per assistenza, limiti e sospensioni.
- [ ] Sito pubblico, termini d'uso, privacy, pagina di stato.
- [ ] Offerta autogestita accanto a quella ospitata (edizione gratuita o a licenza).
- [ ] Posta transazionale (verifica email, riepiloghi) e assistenza.

## Limiti noti
- Offline: la sincronizzazione delle spese in coda non è idempotente; se la risposta del server va persa, una spesa può essere duplicata. Modifica ed eliminazione richiedono rete.
- Backup: due backup nello stesso giorno si sostituiscono; la verifica completa dei backup cifrati richiede la chiave privata nel container.
