#!/usr/bin/env bash
# Cloud Agent install: prepare the yamcs-stack development environment.
#
# Idempotent. Installs the Docker engine (with fuse-overlayfs for nested
# containers), syncs the pinned Python toolchain, and builds the pinned Yamcs
# image so it is cached in the environment snapshot for fast boots.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# --- System packages: Docker engine + fuse-overlayfs -------------------------
if ! command -v docker >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    docker.io docker-compose-v2 fuse-overlayfs uidmap iptables
fi

# The host overlay2 driver is unavailable inside the nested Cloud Agent
# container; fuse-overlayfs works without extra privileges.
sudo mkdir -p /etc/docker
if [ ! -f /etc/docker/daemon.json ] || ! grep -q fuse-overlayfs /etc/docker/daemon.json; then
  echo '{ "storage-driver": "fuse-overlayfs" }' | sudo tee /etc/docker/daemon.json >/dev/null
fi

# Allow the unprivileged agent user to reach the Docker socket.
sudo groupadd -f docker
sudo usermod -aG docker "$(id -un)"

# --- Start the daemon so the image can be built during install ---------------
"$REPO_ROOT/.cursor/start.sh"

# --- Project setup: Python env + Yamcs image ---------------------------------
# `usermod` does not affect the current shell, so run the docker-dependent make
# target under the docker group with `sg`.
cd "$REPO_ROOT/server"
sg docker -c "make setup"
