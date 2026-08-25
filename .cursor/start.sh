#!/usr/bin/env bash
# Cloud Agent start: ensure the Docker daemon is running for this boot.
#
# The Yamcs stack builds and runs containers, so a Docker daemon must be
# available. The daemon process does not survive a snapshot/reboot, so it is
# (re)started on every boot here. This script is idempotent and safe to rerun.
set -euo pipefail

LOG=/var/log/cursor-dockerd.log

if docker info >/dev/null 2>&1 || sudo docker info >/dev/null 2>&1; then
  echo "Docker daemon already running"
  exit 0
fi

# Clear stale runtime state left by a previously running (possibly snapshotted)
# daemon so dockerd does not refuse to start on "pid file found".
sudo rm -f /var/run/docker.pid /run/docker.pid

# Launch dockerd detached so it outlives this script. fuse-overlayfs is used as
# the storage driver (configured in install) because the host overlay driver is
# unavailable inside the nested Cloud Agent container. The log lives in a
# root-owned directory since dockerd runs as root.
sudo bash -c "nohup dockerd >>'$LOG' 2>&1 &"

for _ in $(seq 1 60); do
  if sudo docker info >/dev/null 2>&1; then
    echo "Docker daemon is ready"
    exit 0
  fi
  sleep 1
done

echo "Docker daemon failed to become ready within 60s" >&2
sudo tail -n 60 "$LOG" >&2 || true
exit 1
