#!/usr/bin/env bash
# Updates DataPilot on the server. Run as root: bash scripts/deploy_server.sh
# Safe to run repeatedly. It adds missing settings to .env, but never changes existing
# ones, and it doesn't touch uploaded datasets.
set -euo pipefail

APP=/home/deploy/datapilot
ENV_FILE="$APP/.env"
WORK=/var/lib/datapilot-executor

as_deploy() { sudo -u deploy bash -c "cd $APP && $1"; }

add_setting() {  # name, value: only added if the setting isn't already there.
  grep -q "^$1=" "$ENV_FILE" || printf '%s=%s\n' "$1" "$2" >> "$ENV_FILE"
}

echo "== Getting the latest code"
as_deploy "git pull --ff-only"
as_deploy "\$HOME/.local/bin/uv sync --frozen"

echo "== Preparing the executor user"
id dp-executor >/dev/null 2>&1 || useradd --system --home-dir "$WORK" --shell /usr/sbin/nologin dp-executor
usermod -aG docker,deploy dp-executor
install -d -o dp-executor -g dp-executor -m 755 "$WORK"
install -d -o root -g root -m 700 /etc/datapilot

echo "== Checking settings (values are never printed)"
cp -p "$ENV_FILE" "$ENV_FILE.backup"
if ! grep -q '^EXECUTOR_TOKEN=.' "$ENV_FILE"; then
  sed -i '/^EXECUTOR_TOKEN=/d' "$ENV_FILE"
  printf 'EXECUTOR_TOKEN=%s\n' "$(openssl rand -hex 32)" >> "$ENV_FILE"
fi
add_setting EXECUTOR_URL http://127.0.0.1:8100
# The executor gets only its token, not the rest of .env.
( umask 077; grep '^EXECUTOR_TOKEN=' "$ENV_FILE" > /etc/datapilot/executor.env )

echo "== Keeping AI keys out of the frontend"
# The frontend gets only the backend's address, never .env with its AI keys.
( umask 027; printf 'BACKEND_URL=%s\n' "$(grep '^BACKEND_URL=' "$ENV_FILE" | cut -d= -f2- || true)" > "$APP/frontend.env" )
sed -i 's/^BACKEND_URL=$/BACKEND_URL=http:\/\/127.0.0.1:8000/' "$APP/frontend.env"
chown deploy:deploy "$APP/frontend.env"
install -d /etc/systemd/system/datapilot-frontend.service.d
printf '[Service]\nEnvironmentFile=\nEnvironmentFile=%s/frontend.env\n' "$APP" > /etc/systemd/system/datapilot-frontend.service.d/no-secrets.conf
grep -q '^AI_API_KEY=.' "$ENV_FILE" || echo "WARNING: AI_API_KEY (Groq) isn't set in $ENV_FILE, so analyses won't run."

echo "== Building the sandbox image"
docker build -q -t datapilot-sandbox:1 "$APP/sandbox" >/dev/null

echo "== Restarting services"
install -m 644 "$APP/deploy/datapilot-executor.service" /etc/systemd/system/datapilot-executor.service
systemctl daemon-reload
systemctl enable --now datapilot-executor >/dev/null 2>&1
systemctl restart datapilot-executor datapilot-backend datapilot-frontend

echo "== Checking health"
for attempt in $(seq 1 20); do
  if curl -sf http://127.0.0.1:8000/health >/dev/null \
    && curl -sf http://127.0.0.1:8100/health >/dev/null \
    && curl -sf http://127.0.0.1:8501/_stcore/health >/dev/null; then
    echo "All services are healthy."
    exit 0
  fi
  sleep 2
done
echo "A service didn't become healthy. Check with: journalctl -u datapilot-executor -u datapilot-backend -n 50"
exit 1
