#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

if [[ $# -ne 1 ]]; then
  echo "Uso: bash scripts/restore.sh <cartella-backup>" >&2
  echo "Backup cifrati (.age): AGE_IDENTITY_FILE=/percorso/age-key.txt bash scripts/restore.sh <cartella-backup>" >&2
  exit 2
fi

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
backup_dir="$(cd -- "$1" && pwd)"
cd "$project_dir"

encrypted=false
db_file=database.dump
docs_file=documents.tar.gz
if [[ -f "$backup_dir/database.dump.age" ]]; then
  encrypted=true
  db_file=database.dump.age
  docs_file=documents.tar.gz.age
fi

# Decrypts a backup file to stdout; the plaintext is only ever streamed through pipes.
age_decrypt() {
  local file="$1"
  if command -v age >/dev/null 2>&1; then
    age -d -i "$AGE_IDENTITY_FILE" "$file"
  else
    docker compose run --rm --no-deps -T \
      --user "$(id -u):$(id -g)" \
      -v "$AGE_IDENTITY_FILE:/identity:ro" \
      --entrypoint age backup -d -i /identity < "$file"
  fi
}

if [[ "$encrypted" == true ]]; then
  if [[ -z "${AGE_IDENTITY_FILE:-}" || ! -f "${AGE_IDENTITY_FILE:-}" ]]; then
    echo "Questo backup è cifrato (.age): indica la chiave privata con" >&2
    echo "  AGE_IDENTITY_FILE=/percorso/age-key.txt bash scripts/restore.sh $1" >&2
    exit 1
  fi
  AGE_IDENTITY_FILE="$(cd -- "$(dirname -- "$AGE_IDENTITY_FILE")" && pwd)/$(basename -- "$AGE_IDENTITY_FILE")"
fi

for file in "$db_file" "$docs_file" SHA256SUMS; do
  if [[ ! -f "$backup_dir/$file" ]]; then
    echo "File di backup mancante: $backup_dir/$file" >&2
    exit 1
  fi
done
(
  cd "$backup_dir"
  sha256sum -c SHA256SUMS
)
db_container="$(docker compose ps -aq db | head -n 1)"
api_container="$(docker compose ps -aq api | head -n 1)"
if [[ -z "$db_container" || -z "$api_container" ]]; then
  echo "Crea prima i container con docker compose up -d." >&2
  exit 1
fi

documents_volume="$(docker inspect "$api_container" --format '{{range .Mounts}}{{if eq .Destination "/data/documents"}}{{.Name}}{{end}}{{end}}')"
if [[ -z "$documents_volume" ]]; then
  echo "Non trovo il volume Docker dei documenti." >&2
  exit 1
fi
if ! docker volume inspect "$documents_volume" >/dev/null 2>&1; then
  echo "Il volume dei documenti non esiste: $documents_volume" >&2
  exit 1
fi

echo "ATTENZIONE: il ripristino sostituirà database e documenti attuali."
echo "Backup: $backup_dir"
read -r -p "Scrivi RIPRISTINA per continuare: " confirmation
if [[ "$confirmation" != "RIPRISTINA" ]]; then
  echo "Ripristino annullato."
  exit 0
fi

if [[ "$encrypted" == true ]]; then
  # Verifica la chiave prima di toccare qualsiasi dato corrente.
  if ! age_decrypt "$backup_dir/$docs_file" | tar -tzf - >/dev/null; then
    echo "Impossibile decifrare il backup con la chiave indicata; nulla è stato modificato." >&2
    exit 1
  fi
fi

if ! docker image inspect alpine:3.24 >/dev/null 2>&1; then
  docker pull alpine:3.24
fi

docker compose stop
docker compose up -d db
ready=false
for _ in $(seq 1 30); do
  if docker compose exec -T db sh -c \
    'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null 2>&1; then
    ready=true
    break
  fi
  sleep 2
done
if [[ "$ready" != true ]]; then
  echo "Il database non è diventato disponibile; stack lasciato fermo." >&2
  exit 1
fi

pg_restore_cmd='pg_restore --clean --if-exists --no-owner --no-acl -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
if [[ "$encrypted" == true ]]; then
  age_decrypt "$backup_dir/$db_file" | docker compose exec -T db sh -c "$pg_restore_cmd"
  age_decrypt "$backup_dir/$docs_file" | docker run --rm -i \
    -v "$documents_volume:/destination" \
    alpine:3.24 \
    sh -c 'find /destination -mindepth 1 -maxdepth 1 -exec rm -rf -- {} + && tar -xzf - -C /destination'
else
  docker compose exec -T db sh -c "$pg_restore_cmd" < "$backup_dir/database.dump"
  docker run --rm \
    -v "$documents_volume:/destination" \
    -v "$backup_dir:/backup:ro" \
    alpine:3.24 \
    sh -c 'find /destination -mindepth 1 -maxdepth 1 -exec rm -rf -- {} + && tar -xzf /backup/documents.tar.gz -C /destination'
fi

docker compose up -d
echo "Ripristino completato dallo snapshot: $backup_dir"
