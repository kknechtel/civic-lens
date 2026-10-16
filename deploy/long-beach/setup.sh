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
# Never touches AH's services, database or env file. It borrows the RDS master
# login (from /etc/atlantic-highlands.env or AH's Secrets Manager entry, or
# ADMIN_DATABASE_URL) only to create the civiclens_lb role + database, and
# never stores it: Long Beach runs as civiclens_lb, which can't read AH's data.
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
# The RDS master login is borrowed only for this step (creating Long Beach's
# own role and database) and is never written anywhere. Long Beach runs as
# civiclens_lb, which owns civiclens_lb and nothing else: it can't read AH's
# data, and rotating AH's password doesn't touch it.
ah_admin_url() {
  local u
  u=$(grep -E '^DATABASE_URL=' /etc/atlantic-highlands.env 2>/dev/null | head -1 | cut -d= -f2-)
  if grep -qE '^AWS_SECRETS_NAME=.+' /etc/atlantic-highlands.env 2>/dev/null; then
    # Secrets Manager wins over the env file, exactly as AH's config.py does.
    u=$( set -a; . /etc/atlantic-highlands.env; set +a
         /opt/atlantic-highlands/api/venv/bin/python -c 'import json,os,boto3
s=boto3.client("secretsmanager",region_name=os.getenv("AWS_REGION","us-east-1")).get_secret_value(SecretId=os.environ["AWS_SECRETS_NAME"])
print(json.loads(s["SecretString"]).get("DATABASE_URL",""))' 2>/dev/null || true )
    [ -n "$u" ] || u=$(grep -E '^DATABASE_URL=' /etc/atlantic-highlands.env | head -1 | cut -d= -f2-)
  fi
  echo "${u/postgresql+psycopg2:/postgresql:}"
}
ADMIN_URL="${ADMIN_DATABASE_URL:-$(ah_admin_url)}"
[ -n "$ADMIN_URL" ] || { echo "Can't find AH's DATABASE_URL; run with ADMIN_DATABASE_URL=postgresql://master:pw@host:5432/postgres"; exit 1; }
ADMIN_DB="${ADMIN_URL%/*}/postgres"

if [ ! -f "$ENVF" ]; then
  cp "$HERE/long-beach.env.example" "$ENVF"
  LB_PW=$(openssl rand -hex 24)
  HOSTPART="${ADMIN_URL#*@}"; HOSTPART="${HOSTPART%%/*}"
  sed -i "s#^DATABASE_URL=.*#DATABASE_URL=postgresql://civiclens_lb:$LB_PW@$HOSTPART/civiclens_lb#" "$ENVF"
  sed -i "s#^JWT_SECRET=.*#JWT_SECRET=$(openssl rand -base64 48 | tr -d '\n/+=')#" "$ENVF"
  sed -i "s#^AI_KEYS_SECRET=.*#AI_KEYS_SECRET=$(openssl rand -base64 48 | tr -d '\n/+=')#" "$ENVF"
  sed -i "s#^PUBLIC_ORIGIN=.*#PUBLIC_ORIGIN=https://$DOMAIN#; s#^ALLOWED_ORIGINS=.*#ALLOWED_ORIGINS=https://$DOMAIN#" "$ENVF"
  chmod 600 "$ENVF"
fi
set -a; . "$ENVF"; set +a
LB_PW=$(python3 -c 'import sys,urllib.parse as u; print(u.unquote(u.urlsplit(sys.argv[1]).password or ""))' "$DATABASE_URL")

say "Database civiclens_lb, role civiclens_lb (on the existing RDS instance)"
psql "$ADMIN_DB" -v ON_ERROR_STOP=1 -q <<SQL
SELECT 'CREATE ROLE civiclens_lb LOGIN' WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='civiclens_lb')\\gexec
ALTER ROLE civiclens_lb WITH LOGIN PASSWORD '$LB_PW';
GRANT civiclens_lb TO CURRENT_USER;
SELECT 'CREATE DATABASE civiclens_lb OWNER civiclens_lb' WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname='civiclens_lb')\\gexec
ALTER DATABASE civiclens_lb OWNER TO civiclens_lb;
REVOKE ALL ON DATABASE civiclens_lb FROM PUBLIC;
SQL
# Extensions need the master login; everything after this runs as civiclens_lb.
psql "${ADMIN_URL%/*}/civiclens_lb" -q -c "CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pg_trgm; ALTER SCHEMA public OWNER TO civiclens_lb"
unset ADMIN_URL ADMIN_DB
psql "$DATABASE_URL" -tAc "SELECT 'connected as ' || current_user"

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
