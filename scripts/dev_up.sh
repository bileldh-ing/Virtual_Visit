#!/bin/bash
# Start API + worker locally without Docker (dev sandbox). Requires scripts/sandbox_bootstrap.sh.
# Env overrides: DEVDATA (state dir), STORAGE=local|s3
set -e
R=$(cd "$(dirname "$0")/.." && pwd)
D=${DEVDATA:-/tmp/devdata}
V=${VENV:-/tmp/rsvenv}
mkdir -p "$D"
export ROOMSCAPE_ENV=development
export ROOMSCAPE_DATABASE_URL=${ROOMSCAPE_DATABASE_URL:-postgresql+psycopg://roomscape:roomscape@localhost:5433/roomscape}
export ROOMSCAPE_REDIS_URL=redis://localhost:6379/0
export ROOMSCAPE_SECRET_KEY=${ROOMSCAPE_SECRET_KEY:-dev-only-secret-key-change-me-0123456789}
export ROOMSCAPE_WORKER_WORKSPACE=/tmp/rsjobs
export ROOMSCAPE_BOOTSTRAP_ADMIN_EMAIL=${ROOMSCAPE_BOOTSTRAP_ADMIN_EMAIL:-admin@roomscape.dev}
export ROOMSCAPE_PUBLIC_WEB_URL=http://localhost:3000
export QT_QPA_PLATFORM=offscreen
if [ "${STORAGE:-s3}" = "s3" ] && [ -x "$D/bin/minio" ]; then
  export ROOMSCAPE_STORAGE_BACKEND=s3 ROOMSCAPE_S3_ENDPOINT_URL=http://localhost:9000 ROOMSCAPE_S3_ACCESS_KEY=roomscape ROOMSCAPE_S3_SECRET_KEY=roomscape-dev-secret
  if ! curl -sf http://localhost:9000/minio/health/live >/dev/null; then
    MINIO_ROOT_USER=roomscape MINIO_ROOT_PASSWORD=roomscape-dev-secret setsid nohup "$D/bin/minio" server "$D/minio" --address :9000 --console-address :9001 > "$D/minio.log" 2>&1 < /dev/null &
    sleep 4
  fi
else
  export ROOMSCAPE_STORAGE_BACKEND=local ROOMSCAPE_STORAGE_LOCAL_ROOT=$D/storage
fi
cd "$R/services/api"
"$V/bin/python" -m roomscape_api.migrate
pkill -f "uvicorn roomscape_api.main" 2>/dev/null || true
pkill -f "roomscape_worker.main" 2>/dev/null || true
setsid nohup "$V/bin/uvicorn" roomscape_api.main:app --host 0.0.0.0 --port 8000 > "$D/api.log" 2>&1 < /dev/null &
setsid nohup "$V/bin/python" -m roomscape_worker.main > "$D/worker.log" 2>&1 < /dev/null &
sleep 3
curl -sf http://localhost:8000/readyz && echo
