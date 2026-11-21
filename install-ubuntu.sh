#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script with sudo: sudo bash /opt/vialenne-vr/install-ubuntu.sh" >&2
  exit 1
fi

APPDIR=/opt/vialenne-vr
DATADIR=/srv/storage/vr-library

[[ -f "$APPDIR/app.py" ]] || { echo "Missing $APPDIR/app.py — unzip the package into /opt first" >&2; exit 1; }
command -v python3 >/dev/null || { echo "Python 3 is required" >&2; exit 1; }
command -v systemctl >/dev/null || { echo "Systemd is required" >&2; exit 1; }

if ! id -u vialenne-vr >/dev/null 2>&1; then
  useradd --system --user-group --home-dir /var/lib/vialenne-vr --shell /usr/sbin/nologin vialenne-vr
fi
install -d -m 0750 -o vialenne-vr -g vialenne-vr "$DATADIR" "$DATADIR/covers" "$DATADIR/packages"
# Admin-configured single upstream; user credentials are never exposed to web.
install -d -m 0750 -o root -g vialenne-vr /etc/vialenne-vr
# Optional dependencies for VRSRC metadata sync; keep upgrade safe if offline.
if command -v apt-get >/dev/null; then
  DEBIAN_FRONTEND=noninteractive apt-get install -y rclone p7zip-full
fi

# Do not touch Copyparty/Caddy or ports 80/443. Dedicated loopback-only service.
cat > /etc/systemd/system/vialenne-vr.service <<'SERVICE'
[Unit]
Description=Vialenne VR Single-Source Library
After=network.target
RequiresMountsFor=/srv/storage/vr-library

[Service]
Type=simple
User=vialenne-vr
Group=vialenne-vr
WorkingDirectory=/opt/vialenne-vr
ExecStart=/usr/bin/python3 /opt/vialenne-vr/app.py --data-dir /srv/storage/vr-library serve --host 127.0.0.1 --port 4010
Restart=on-failure
RestartSec=4
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/srv/storage/vr-library
UMask=0077

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable --now vialenne-vr
systemctl restart vialenne-vr
sleep 1
systemctl is-active --quiet vialenne-vr || {
  systemctl status vialenne-vr --no-pager -l
  echo "Service did not start" >&2
  exit 1
}
if command -v curl >/dev/null; then
  curl --fail --silent --show-error http://127.0.0.1:4010/api/status
  echo
fi
printf '%s\n' 'Vialenne VR installed on 127.0.0.1:4010.' 'Configure /etc/vialenne-vr/ServerInfo.json on the server to enable the single VRSRC source.'
