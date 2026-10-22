#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"

backup_dir="${1:-"$project_dir/backups/$(date -u +%Y%m%dT%H%M%SZ)"}"
if ! mkdir -p -- "$backup_dir"; then
  echo "Impossibile creare la cartella di backup: $backup_dir" >&2
  exit 1
fi
backup_dir="$(cd -- "$backup_dir" && pwd)"

db_container="$(docker compose ps -q db)"
api_container="$(docker compose ps -q api)"
if [[ -z "$db_container" || -z "$api_container" ]]; then
  echo "Avvia prima lo stack con docker compose up -d." >&2
  exit 1
fi

documents_volume="$(docker inspect "$api_container" --format '{{range .Mounts}}{{if eq .Destination "/data/documents"}}{{.Name}}{{end}}{{end}}')"
if [[ -z "$documents_volume" ]]; then
  echo "Non trovo il volume Docker dei documenti." >&2
  exit 1
fi
if [[ -e "$backup_dir/database.dump" || -e "$backup_dir/documents.tar.gz" ]]; then
  echo "La cartella contiene già file di backup; scegli una cartella vuota." >&2
  exit 1
fi

docker compose exec -T db sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$backup_dir/database.dump"
docker run --rm \
  -v "$documents_volume:/source:ro" \
  -v "$backup_dir:/backup" \
  alpine:3.24 \
  tar -czf /backup/documents.tar.gz -C /source .

(
  cd "$backup_dir"
  sha256sum database.dump documents.tar.gz > SHA256SUMS
)
echo "Backup completato: $backup_dir"
