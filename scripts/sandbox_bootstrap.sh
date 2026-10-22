#!/bin/bash
# Re-provision an ephemeral Debian 13 dev sandbox (no Docker): COLMAP, PostgreSQL, Redis, Python venv.
# Idempotent. For normal development use docker compose instead (see README).
set -e
R=$(cd "$(dirname "$0")/.." && pwd)
if ! command -v colmap >/dev/null || ! command -v redis-server >/dev/null || [ ! -d /usr/lib/postgresql ]; then
  sudo -n apt-get update -qq 2>/dev/null || true
  sudo -n DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends colmap redis-server postgresql >/dev/null 2>&1
fi
V=${VENV:-/tmp/rsvenv}
if [ ! -x "$V/bin/roomscape-recon" ]; then
  python3 -m venv "$V"
  "$V/bin/pip" install -q opencv-python-headless numpy pillow fastapi "uvicorn[standard]" "sqlalchemy>=2" alembic "psycopg[binary]" \
    "pydantic[email]" pydantic-settings argon2-cffi rq redis boto3 python-multipart httpx pytest pytest-cov ruff mypy
  "$V/bin/pip" install -q --no-deps -e "$R/services/reconstruction" -e "$R/services/api" -e "$R/services/worker"
fi
PG=/usr/lib/postgresql/17/bin
if [ ! -d /tmp/pg ]; then
  $PG/initdb -D /tmp/pg -U postgres -A trust >/dev/null
  printf "unix_socket_directories='/tmp'\nshared_buffers=32MB\n" >> /tmp/pg/postgresql.conf
fi
$PG/pg_ctl -D /tmp/pg status >/dev/null 2>&1 || $PG/pg_ctl -D /tmp/pg -l /tmp/pg.log -o "-p 5432 -k /tmp" start >/dev/null
sleep 2
psql -h /tmp -U postgres -tc "select 1 from pg_roles where rolname='roomscape'" | grep -q 1 || \
  psql -h /tmp -U postgres -c "create user roomscape with password 'roomscape' createdb;" -c "create database roomscape owner roomscape;" -c "create database roomscape_test owner roomscape;" >/dev/null
redis-cli ping >/dev/null 2>&1 || redis-server --port 6379 --save "" --daemonize yes >/dev/null
echo "bootstrap ok"
