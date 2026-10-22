#!/bin/bash
# Re-provision the dev sandbox (Debian 13, aarch64, no GPU). Idempotent.
set -e
sudo -n apt-get update -qq 2>/dev/null || true
sudo -n DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends colmap redis-server postgresql 2>&1 | tail -1
