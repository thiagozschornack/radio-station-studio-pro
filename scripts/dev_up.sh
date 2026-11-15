#!/usr/bin/env bash
# Ambiente LOCAL de desenvolvimento, sem Docker: SQLite + servidor de teste mini-icecast.
# Uso: ./scripts/dev_up.sh   (Ctrl+C encerra tudo)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"
export PYTHONPATH="$ROOT/backend" RSSP_DATABASE_URL="sqlite:///$ROOT/data/dev.db" RSSP_STORAGE_PATH="$ROOT/data/storage"
ICE_PW="${ICE_PW:-dev-source-pass}"
python3 -m rssp.cli migrate
if ! python3 -c "from rssp.db import get_db; import sys; sys.exit(0 if get_db().scalar('SELECT COUNT(*) AS c FROM users') else 1)"; then
  python3 -m rssp.cli create-superadmin --email admin@local.dev --name Admin --password "${ADMIN_PW:-TroqueEssaSenha1}"
  python3 -m rssp.cli seed-demo --owner admin@local.dev
  python3 - <<PY
from rssp.db import get_db; from rssp.security import encrypt
db=get_db(); sid=db.scalar("SELECT id FROM stations WHERE slug='demo-thz'")
db.update("stream_configs",{"host":"127.0.0.1","port":8000,"source_password_enc":encrypt("$ICE_PW"),
  "public_listen_url":"http://127.0.0.1:8000/demo-thz","enabled":1},"station_id=?",(sid,))
PY
fi
trap 'kill 0' EXIT
python3 "$ROOT/tools/mini_icecast.py" --port 8000 --password "$ICE_PW" &
python3 -m rssp.jobs &
python3 -m rssp.playout --all &
echo "Painel: http://127.0.0.1:8080/admin/  (admin@local.dev)   Player: http://127.0.0.1:8080/r/demo-thz"
python3 -m uvicorn rssp.api.app:app --host 127.0.0.1 --port 8080
