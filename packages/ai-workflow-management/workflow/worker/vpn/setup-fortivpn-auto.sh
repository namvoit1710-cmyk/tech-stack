#!/usr/bin/env bash
#
# GIAI DOAN 2 — bien tunnel GitLab thanh dich vu tu chay, khong can nguoi bam.
# Chay SAU setup-fortivpn-gitlab.sh:  sudo bash setup-fortivpn-auto.sh
#
# --- Y TUONG ---
# "Dang nhap SAML" khong phai mot hanh dong lien khoi. No la ba tang het han
# chong len nhau, va chi tang tren cung moi can con nguoi:
#
#   tunnel PPP            vai phut mat mang    -> systemd Restart=always
#   SVPNCOOKIE (FortiGate) auth-timeout <= 3 ngay -> xin lai qua luong SAML
#   phien Entra ID         90 ngay cuon (KMSI)  -> IM LANG, khong hoi gi
#
# Nen chi phai dang nhap tay MOT LAN. Tu do ve sau, moi lan tunnel rot,
# systemd chay lai, Chromium headless di lai luong SAML bang profile cu, Entra
# cho qua khong hoi, va tunnel len lai.

set -euo pipefail

BROWSER_USER="${BROWSER_USER:-laidonit}"
APP=/opt/fortivpn-saml
VENV=$APP/venv
SAML_PORT=8020
HERE=$(cd "$(dirname "$0")" && pwd)

[ "$(id -u)" -eq 0 ] || { echo "Phai chay bang: sudo bash $0" >&2; exit 1; }
id "$BROWSER_USER" >/dev/null 2>&1 || { echo "Khong co user $BROWSER_USER" >&2; exit 1; }

# openfortivpn khai bao `bin_PROGRAMS` nen no vao <prefix>/bin, KHONG phai
# sbin. Do la thu de doan nham, nen o day DO chu khong gia dinh.
VPNBIN=""
for p in /usr/local/bin/openfortivpn /usr/local/sbin/openfortivpn \
         /usr/bin/openfortivpn /usr/sbin/openfortivpn; do
  [ -x "$p" ] && { VPNBIN="$p"; break; }
done
[ -n "$VPNBIN" ] || VPNBIN=$(command -v openfortivpn 2>/dev/null || true)

# Giai doan 1 phai xong truoc — khong doan, kiem that.
[ -n "$VPNBIN" ] || { echo "Khong thay openfortivpn — chay setup-fortivpn-gitlab.sh truoc." >&2; exit 1; }
"$VPNBIN" --help 2>&1 | grep -q -- '--saml-login' \
  || { echo "$VPNBIN khong co --saml-login — chay lai setup-fortivpn-gitlab.sh." >&2; exit 1; }
[ -e /etc/openfortivpn/gitlab.conf ] \
  || { echo "Thieu /etc/openfortivpn/gitlab.conf — chay setup-fortivpn-gitlab.sh truoc." >&2; exit 1; }
[ -f "$HERE/saml_login.py" ] || { echo "Thieu $HERE/saml_login.py" >&2; exit 1; }
echo "==> dung openfortivpn: $VPNBIN ($("$VPNBIN" --version))"

echo "==> 1/5 Cai python venv"
apt-get update -qq
apt-get install -y --no-install-recommends python3-venv python3-pip

echo "==> 2/5 Dung moi truong Playwright (duoi quyen $BROWSER_USER)"
install -d -o "$BROWSER_USER" -g "$BROWSER_USER" -m 0755 "$APP"
install -o "$BROWSER_USER" -g "$BROWSER_USER" -m 0755 "$HERE/saml_login.py" "$APP/saml_login.py"
runuser -u "$BROWSER_USER" -- python3 -m venv "$VENV"
runuser -u "$BROWSER_USER" -- "$VENV/bin/pip" install --quiet --upgrade pip playwright

echo "==> 3/5 Tai Chromium + thu vien he thong"
# install-deps can root; ban than browser thi tai duoi quyen user de nam trong
# ~/.cache/ms-playwright cua chinh user se chay no.
"$VENV/bin/playwright" install-deps chromium
runuser -u "$BROWSER_USER" -- "$VENV/bin/playwright" install chromium

echo "==> 4/5 Ghi orchestrator"
cat > /usr/local/bin/gitlab-vpn-auto <<HELPER
#!/bin/sh
# Dung tunnel toi GitLab va tu di qua luong SAML. Khong co tham so.
set -eu

# THU TU QUAN TRONG: openfortivpn phai chay TRUOC, vi chinh no la cai mo cong
# $SAML_PORT va ngoi doi ket qua SAML. Neu dan browser di truoc, chuyen huong
# cuoi se roi vao khoang khong.
$VPNBIN -c /etc/openfortivpn/gitlab.conf \\
  --saml-login=$SAML_PORT --pppd-ipparam=gitlab-vpn &
VPN=\$!
trap 'kill \$VPN 2>/dev/null || true' EXIT INT TERM

if ! runuser -u $BROWSER_USER -- $VENV/bin/python $APP/saml_login.py; then
  echo "khong lay duoc phien SAML — xem thong bao [saml] o tren" >&2
  exit 1
fi

trap - EXIT
wait \$VPN
HELPER
chmod 0755 /usr/local/bin/gitlab-vpn-auto

echo "==> 5/5 Ghi systemd unit"
cat > /etc/systemd/system/gitlab-vpn.service <<'UNIT'
[Unit]
Description=Tunnel toi GitLab cong ty (FortiGate SSL-VPN, SAML tu dong)
Documentation=file:///opt/fortivpn-saml/saml_login.py
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/gitlab-vpn-auto
# Restart=always chu KHONG phai on-failure: khi FortiGate het auth-timeout
# (toi da 3 ngay) openfortivpn thoat SACH voi ma 0 — van phai noi lai.
Restart=always
RestartSec=30
# Chan vong lap chay dien neu Entra bat dau hoi lai: dung han sau 5 lan
# that bai trong 30 phut, de nguoi con doc duoc log thay vi bi troi di.
StartLimitIntervalSec=1800
StartLimitBurst=5

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload

cat <<DONE

================================================================
Xong. Con DUNG MOT viec phai lam bang tay, va chi mot lan.

  1. Ngoi truoc man hinh may nay (khong lam qua SSH duoc), mo terminal:

       $VENV/bin/python $APP/saml_login.py --login

     Chromium se mo ra. Dang nhap SSO, va NHO TICK "Stay signed in"
     — do chinh la thu sinh ra cookie 90 ngay.
     Thay dong "[saml] SAML xong" la duoc.

  2. Bat dich vu:

       sudo systemctl enable --now gitlab-vpn.service

  3. Kiem:

       systemctl status gitlab-vpn.service
       ip route | grep ppp
       curl -sI https://gitlab.simplemdg.com/ | head -1

  4. Kiem worker KHONG bi keo qua VPN (quan trong nhat):

       ip route get 1.1.1.1      # phai van qua wlp0s20f3
       curl -s https://api.ipify.org   # phai van la IP nha

  5. Thu that — cat tunnel roi xem no co tu len lai khong:

       sudo systemctl restart gitlab-vpn.service
       journalctl -u gitlab-vpn.service -f
================================================================
DONE
