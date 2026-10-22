#!/bin/bash
# Dev-sandbox supervisor: keeps Postgres, Redis, API and worker alive on non-default ports
# (the sandbox host is shared and may reap background processes). Not for production.
R=$(cd "$(dirname "$0")/.." && pwd)
V=${VENV:-/tmp/rsvenv}
D=/tmp/devdata
mkdir -p $D
PG=/usr/lib/postgresql/17/bin
export ROOMSCAPE_ENV=development
export ROOMSCAPE_DATABASE_URL=postgresql+psycopg://roomscape:roomscape@localhost:18433/roomscape
export ROOMSCAPE_REDIS_URL=redis://localhost:18379/0
export ROOMSCAPE_SECRET_KEY=dev-only-secret-key-change-me-0123456789
export ROOMSCAPE_WORKER_WORKSPACE=/tmp/rsjobs
export ROOMSCAPE_BOOTSTRAP_ADMIN_EMAIL=admin@roomscape.dev
export ROOMSCAPE_PUBLIC_WEB_URL=http://localhost:18430
export ROOMSCAPE_CORS_ORIGINS='["http://localhost:18430"]'
export ROOMSCAPE_STORAGE_BACKEND=local ROOMSCAPE_STORAGE_LOCAL_ROOT=$D/storage
export QT_QPA_PLATFORM=offscreen
if [ ! -d $D/pg ]; then
  $PG/initdb -D $D/pg -U postgres -A trust >/dev/null
  printf "unix_socket_directories='/tmp'\nshared_buffers=32MB\nport=18433\n" >> $D/pg/postgresql.conf
  $PG/pg_ctl -D $D/pg -l $D/pg.log start >/dev/null; sleep 2
  psql -h /tmp -p 18433 -U postgres -c "create user roomscape with password 'roomscape' createdb;" -c "create database roomscape owner roomscape;" >/dev/null
fi
while true; do
  $PG/pg_ctl -D $D/pg status >/dev/null 2>&1 || { rm -f $D/pg/postmaster.pid; $PG/pg_ctl -D $D/pg -l $D/pg.log start >/dev/null; sleep 2; }
  redis-cli -p 18379 ping >/dev/null 2>&1 || redis-server --port 18379 --save "" --daemonize yes >/dev/null
  if ! curl -sf -m 3 http://localhost:18400/healthz >/dev/null; then
    (cd $R/services/api && $V/bin/python -m roomscape_api.migrate >/dev/null 2>&1; nohup $V/bin/uvicorn roomscape_api.main:app --host 0.0.0.0 --port 18400 >> $D/api.log 2>&1 &)
  fi
  pgrep -f "roomscape_worker.main" >/dev/null || (cd $R/services/api && nohup $V/bin/python -m roomscape_worker.main >> $D/worker.log 2>&1 &)
  sleep 5
done
