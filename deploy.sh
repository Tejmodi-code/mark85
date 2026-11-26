#!/usr/bin/env bash
# One command to build and (re)start the whole stack on a server that has Docker.
set -euo pipefail
cd "$(dirname "$0")"

command -v docker >/dev/null || { echo "Install Docker first: https://docs.docker.com/engine/install/"; exit 1; }

if [ ! -f .env ]; then
  cp .env.example .env
  sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$(openssl rand -hex 24)/" .env
  sed -i "s/^ADMIN_KEY=.*/ADMIN_KEY=$(openssl rand -hex 24)/" .env
  echo "Created .env with fresh secrets. Set SITE_HOST to your domain after DNS points to this server. Keep .env private."
fi

site_host=$(grep -E '^SITE_HOST=' .env | cut -d= -f2- | tr -d '\r')
postgres_password=$(grep -E '^POSTGRES_PASSWORD=' .env | cut -d= -f2- | tr -d '\r')
admin_key=$(grep -E '^ADMIN_KEY=' .env | cut -d= -f2- | tr -d '\r')
if [[ -z "$site_host" || "$site_host" == "example.com" || "$site_host" == "localhost" ]]; then
  echo "Set SITE_HOST in .env to a public DNS name you control before deployment." >&2
  exit 1
fi
if [[ ${#postgres_password} -lt 32 || ${#admin_key} -lt 32 || "$postgres_password" == "change-me" || "$admin_key" == "change-me" ]]; then
  echo "Set strong, unique POSTGRES_PASSWORD and ADMIN_KEY values in .env." >&2
  exit 1
fi

docker compose build
set -a; . ./.env; set +a
docker compose up -d --remove-orphans --scale api="${API_REPLICAS:-4}" --scale engine="${ENGINE_REPLICAS:-2}"

# nginx resolves the api replicas when it starts, so reload it after scaling or restarting them.
docker compose exec -T nginx nginx -s reload

echo "Waiting for the site to report ready..."
port=$(grep -E '^HTTPS_PORT=' .env | cut -d= -f2); port=${port:-443}
for _ in $(seq 1 40); do
  if curl -kfs "https://${site_host}:${port}/readyz" --resolve "${site_host}:${port}:127.0.0.1" >/dev/null; then echo "Ready: https://${site_host}"; docker compose ps; exit 0; fi
  sleep 3
done
echo "Not ready yet. Check: docker compose logs --tail=80 api live nginx"; exit 1
