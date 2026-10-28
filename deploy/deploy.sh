#!/usr/bin/env bash
# Deploys the committed tree to a host over SSH and (re)starts the compose
# project. Secrets are generated on the host on first deploy and kept across
# deploys; named volumes keep keys and the source cache.
#
#   deploy/deploy.sh <ssh-host> [remote-dir]     e.g. deploy/deploy.sh moqx-001
set -euo pipefail

host=${1:?usage: deploy/deploy.sh <ssh-host> [remote-dir]}
dir=${2:-moq-test-tracks}
root=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)
rev=$(git -C "$root" rev-parse --short HEAD)
if ! git -C "$root" diff --quiet HEAD; then
    echo "note: uncommitted changes are not deployed (deploying $rev)" >&2
fi

ssh "$host" "rm -rf '$dir.new' && mkdir -p '$dir.new'"
git -C "$root" archive --format=tar HEAD | ssh "$host" "tar -x -C '$dir.new'"

ssh "$host" bash -s -- "$dir" "$rev" <<'REMOTE'
set -euo pipefail
dir=$1 rev=$2
if [ -f "$dir/deploy/.env" ]; then
    cp -p "$dir/deploy/.env" "$dir.new/deploy/.env"
fi
rm -rf "$dir.old"
if [ -d "$dir" ]; then mv "$dir" "$dir.old"; fi
mv "$dir.new" "$dir"
cd "$dir/deploy"
if [ ! -f .env ]; then
    cp .env.example .env
fi
chmod 600 .env
for var in MTT_RUNNER_TOKEN MTT_SESSION_SECRET MTT_GRAFANA_ADMIN_PASSWORD; do
    if ! grep -q "^$var=" .env; then
        echo "$var=" >> .env
    fi
    if ! grep -q "^$var=." .env; then
        sed -i "s/^$var=.*/$var=$(openssl rand -hex 32)/" .env
    fi
done
# node-exporter listens on the obs bridge address; only that subnet may reach it.
if command -v ufw >/dev/null && sudo -n true 2>/dev/null; then
    if ! sudo ufw status | grep -q "^172.31.250.1 9100/tcp"; then
        sudo ufw allow from 172.31.250.0/24 to 172.31.250.1 port 9100 proto tcp \
            comment "mtt node-exporter" >/dev/null
    fi
fi
echo "$rev" > ../REVISION
docker compose build --pull
docker compose up -d --remove-orphans
for _ in $(seq 1 30); do
    if docker compose exec -T api python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3)" 2>/dev/null; then
        echo "deployed $rev"
        docker compose ps --format "table {{.Service}}\t{{.Status}}"
        exit 0
    fi
    sleep 2
done
echo "api did not become healthy" >&2
docker compose logs --tail 50 >&2
exit 1
REMOTE
