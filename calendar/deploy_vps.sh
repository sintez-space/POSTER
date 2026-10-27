#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/poster"
REPO_URL="https://github.com/sintez-space/POSTER.git"

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip git nginx

if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch origin
  git -C "$APP_DIR" reset --hard origin/main
else
  rm -rf "$APP_DIR"
  git clone "$REPO_URL" "$APP_DIR"
fi

python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/calendar/requirements.txt"

cat >/etc/systemd/system/poster-calendar.service <<'EOF'
[Unit]
Description=POSTER Calendar FastAPI Service
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/poster
ExecStart=/opt/poster/.venv/bin/uvicorn calendar.app.main:app --host 127.0.0.1 --port 8767 --workers 1
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable poster-calendar.service
systemctl restart poster-calendar.service

cat >/etc/nginx/sites-available/poster-calendar <<'EOF'
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;

    client_max_body_size 200M;
    client_body_timeout 300s;
    sendfile on;
    tcp_nopush on;

    gzip on;
    gzip_types text/plain text/css application/json application/javascript;

    location / {
        proxy_pass http://127.0.0.1:8767;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_request_buffering off;
        proxy_buffering off;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }
}
EOF

rm -f /etc/nginx/sites-enabled/default
ln -sf /etc/nginx/sites-available/poster-calendar /etc/nginx/sites-enabled/poster-calendar
nginx -t
systemctl restart nginx

if command -v ufw >/dev/null 2>&1; then
  ufw allow 80/tcp || true
  ufw allow 22/tcp || true
fi

echo "=== POSTER Calendar deployed on port 80 ==="
