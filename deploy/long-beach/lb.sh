#!/usr/bin/env bash
# Day-to-day commands for the Long Beach deployment. Run with sudo.
#   sudo bash lb.sh invite [email]   one-time invite link (optionally locked to an email)
#   sudo bash lb.sh status           services + memory
#   sudo bash lb.sh logs             follow API logs
#   sudo bash lb.sh update           pull code, deps, rebuild web, restart
#   sudo bash lb.sh restart
set -euo pipefail
APP=/opt/long-beach; ENVF=/etc/long-beach.env
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
run_py() { (set -a; . "$ENVF"; set +a; cd "$APP/api" && runuser -u www-data --preserve-environment -- venv/bin/python "$@"); }
case "${1:-}" in
  invite)  shift; run_py -m scripts.create_invite ${1:+--email "$1"} ;;
  status)  systemctl --no-pager status lb-api lb-web | grep -E "●|Active|Memory"; systemctl --no-pager status lb-rehydrate | grep -E "Active" || true ;;
  logs)    journalctl -u lb-api -f ;;
  restart) systemctl restart lb-api lb-web ;;
  update)
    git -C "$APP" pull --ff-only
    "$APP/api/venv/bin/pip" install -q -r "$APP/api/requirements.txt"
    (set -a; . "$ENVF"; set +a; cd "$APP/api" && venv/bin/python -c "from database import init_db; init_db()")
    cd "$APP/web" && npm ci --no-audit --no-fund && systemd-run --scope --quiet -p MemoryMax=1800M -p CPUQuota=150% \
      env NODE_ENV=production NODE_OPTIONS=--max-old-space-size=1536 npx next build
    cp -r public .next/standalone/ 2>/dev/null || true; mkdir -p .next/standalone/.next && cp -r .next/static .next/standalone/.next/
    chown -R www-data:www-data "$APP"; systemctl restart lb-api lb-web ;;
  *) sed -n '2,8p' "$0" ;;
esac
