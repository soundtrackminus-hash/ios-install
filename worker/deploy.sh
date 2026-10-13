#!/bin/sh
# Деплой Worker'а ios-install-qr (мерж catalog.json + catalog-other.json).
# Токен берётся из CLOUDFLARE_API_TOKEN или из файла /root/.cf_workers_token.
# Нужен токен с правами: Workers Scripts:Edit, Workers KV Storage:Edit
# (шаблон Cloudflare "Edit Cloudflare Workers").
set -e
cd "$(dirname "$0")"
if [ -z "$CLOUDFLARE_API_TOKEN" ] && [ -f /root/.cf_workers_token ]; then
  CLOUDFLARE_API_TOKEN=$(cat /root/.cf_workers_token)
fi
[ -n "$CLOUDFLARE_API_TOKEN" ] || { echo "нет CLOUDFLARE_API_TOKEN (env или /root/.cf_workers_token)"; exit 1; }
export CLOUDFLARE_API_TOKEN
npx --yes wrangler deploy
