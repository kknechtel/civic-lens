#!/usr/bin/env bash
# Long Beach on the shared Atlantic Highlands EC2 box — one-shot, idempotent.
#
#   sudo REPO_URL=https://<token>@github.com/kknechtel/civic-lens.git \
#        bash setup.sh /path/to/long-beach-corpus.jsonl.gz
#
# Does: code → /opt/long-beach, its own database (civiclens_lb) on the
# existing RDS instance, imports the already-processed corpus (no OCR on this
# box), creates the admin account, builds the web app under a memory cap,
# installs capped systemd services + nginx + TLS for longbeach.ahnj.info.
# Never touches AH's services, database or env file (it only READS the RDS
# host/credentials from /etc/atlantic-highlands.env).
set -euo pipefail

CORPUS="${1:-}"
DOMAIN="${DOMAIN:-longbeach.ahnj.info}"
ADMIN_EMAIL="${ADMIN_EMAIL:-karl@rkc.llc}"
REPO_URL="${REPO_URL:-https://github.com/kknechtel/civic-lens.git}"
APP=/opt/long-beach
ENVF=/etc/long-beach.env
HERE="$(cd "$(dirname "$0")" && pwd)"

[ "$(id -u)" = 0 ] || { echo "run as root (sudo)"; exit 1; }
say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

say "System packages"
command -v psql >/dev/null || apt-get install -y postgresql-client
command -v node >/dev/null && node -v | grep -qE '^v(2[0-9])' || {
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && apt-get install -y nodejs; }
command -v certbot >/dev/null || apt-get install -y certbot python3-certbot-nginx

say "Code → $APP"
if [ -d "$APP/.git" ]; then git -C "$APP" pull --ff-only; else git clone --depth 50 "$REPO_URL" "$APP"; fi

say "Environment ($ENVF)"
if [ ! -f "$ENVF" ]; then
  cp "$HERE/long-beach.env.example" "$ENVF"
  # Same RDS instance and credentials as AH, different database.
  AH_URL=$(grep -E '^DATABASE_URL=' /etc/atlantic-highlands.env | head -1 | cut -d= -f2-)
  [ -n "$AH_URL" ] || { echo "No DATABASE_URL in /etc/atlantic-highlands.env; set it in $ENVF by hand"; exit 1; }
  LB_URL="${AH_URL%/*}/civiclens_lb"
  sed -i "s#^DATABASE_URL=.*#DATABASE_URL=$LB_URL#" "$ENVF"
  sed -i "s#^JWT_SECRET=.*#JWT_SECRET=$(openssl rand -base64 48 | tr -d '\n/+=')#" "$ENVF"
  sed -i "s#^AI_KEYS_SECRET=.*#AI_KEYS_SECRET=$(openssl rand -base64 48 | tr -d '\n/+=')#" "$ENVF"
  sed -i "s#^PUBLIC_ORIGIN=.*#PUBLIC_ORIGIN=https://$DOMAIN#; s#^ALLOWED_ORIGINS=.*#ALLOWED_ORIGINS=https://$DOMAIN#" "$ENVF"
  chmod 600 "$ENVF"
fi
set -a; . "$ENVF"; set +a

say "Database civiclens_lb (on the existing RDS instance)"
ADMIN_DB="${DATABASE_URL%/*}/postgres"
psql "$ADMIN_DB" -tAc "SELECT 1 FROM pg_database WHERE datname='civiclens_lb'" | grep -q 1 \
  || psql "$ADMIN_DB" -c "CREATE DATABASE civiclens_lb"
psql "$DATABASE_URL" -c "CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pg_trgm"

say "Python environment"
cd "$APP/api"
[ -x venv/bin/python ] || python3 -m venv venv
venv/bin/pip install -q --upgrade pip
venv/bin/pip install -q -r requirements.txt
mkdir -p "$LOCAL_STORAGE_DIR"; chown -R www-data:www-data /var/lib/long-beach

say "Schema + documents"
venv/bin/python -c "from database import init_db; init_db()"
if [ -n "$CORPUS" ]; then
  venv/bin/python -m scripts.load_fixture_corpus long-beach-ny --file "$CORPUS" --project "Long Beach Scraped Documents"
fi

say "Admin account ($ADMIN_EMAIL)"
if psql "$DATABASE_URL" -tAc "SELECT 1 FROM users WHERE email='$ADMIN_EMAIL' AND is_admin" | grep -q 1; then
  echo "exists"
else
  venv/bin/python create_admin.py "$ADMIN_EMAIL"
fi

say "Web app (built under a 1.8 GB cap so AH keeps running)"
cd "$APP/web"
npm ci --no-audit --no-fund
systemd-run --scope --quiet -p MemoryMax=1800M -p CPUQuota=150% \
  env NODE_ENV=production NODE_OPTIONS=--max-old-space-size=1536 npx next build
cp -r public .next/standalone/ 2>/dev/null || true
mkdir -p .next/standalone/.next && cp -r .next/static .next/standalone/.next/
chown -R www-data:www-data "$APP"

say "Services"
cp "$HERE"/lb-api.service "$HERE"/lb-web.service "$HERE"/lb-rehydrate.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now lb-api lb-web
systemctl restart lb-api lb-web

say "nginx + TLS for $DOMAIN"
sed "s/longbeach.ahnj.info/$DOMAIN/g" "$HERE/nginx-longbeach.conf" > /etc/nginx/sites-available/longbeach
ln -sf /etc/nginx/sites-available/longbeach /etc/nginx/sites-enabled/longbeach
nginx -t && systemctl reload nginx
certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$ADMIN_EMAIL" --redirect || \
  echo "certbot failed: is the DNS A record for $DOMAIN pointing at this box yet? Re-run: certbot --nginx -d $DOMAIN"

say "Fetching original PDFs in the background (hours; safe to leave running)"
systemctl start --no-block lb-rehydrate

say "Done"
curl -fsS -o /dev/null -w "API: %{http_code}\n" http://127.0.0.1:8102/api/municipality || true
echo "Site:   https://$DOMAIN   (invite-only)"
echo "Invite your friend:  sudo bash $HERE/lb.sh invite"
