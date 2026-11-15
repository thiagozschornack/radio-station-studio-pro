#!/bin/sh
# Backup do banco e da biblioteca (Docker Compose). Agende via cron e copie para fora da VPS.
set -eu
DEST="${1:-./backups}/$(date +%Y%m%d-%H%M%S)"; mkdir -p "$DEST"
docker compose exec -T db pg_dump -U rssp -Fc rssp > "$DEST/rssp.dump"
docker compose run --rm -T --no-deps -v "$(cd "$DEST" && pwd)":/out api sh -c "tar czf /out/storage.tgz -C /data storage"
echo "backup em $DEST"
# Restauração: docker compose exec -T db pg_restore -U rssp -d rssp --clean < rssp.dump
#              docker compose run --rm -T --no-deps -v \$PWD:/in api sh -c "tar xzf /in/storage.tgz -C /data"
