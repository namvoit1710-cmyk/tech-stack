#!/usr/bin/env bash
#
# Cai Tailscale de SSH ve may nay tu ben ngoai.
# Chay: sudo bash setup-tailscale.sh
#
# --- VI SAO KHONG MO PORT TREN ROUTER ---
# Nha nay nam sau CGNAT. Tracepath cho thay hop sau router la 100.123.1.164,
# nam trong dai 100.64.0.0/10 danh rieng cho CGNAT. Nghia la router KHONG giu
# 118.69.12.194 — dia chi do la cua nha mang, chia cho nhieu ho. Khong co cong
# nao tu Internet dan toi day de ma mo, va DDNS cung vo nghia.
#
# Tailscale goi RA ngoai roi giu ket noi, nen khong can mo cong nao.
#
# --- VI SAO --accept-routes=false VA --accept-dns=false ---
# Ca hai deu co the chen route hoac viet lai /etc/resolv.conf, keo traffic cua
# claude-worker/log-worker di duong khac — dung thu da mat cong tranh khi cau
# hinh VPN GitLab (xem ../vpn/README.md). Ta chi can chieu NGOAI VAO: SSH tu
# laptop ve day. Khong can chieu nguoc lai.

set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Phai chay bang: sudo bash $0" >&2; exit 1; }

echo "==> Chup lai dinh tuyen TRUOC khi cai, de con doi chieu"
BEFORE_OUT=$(ip route get 1.1.1.1 2>/dev/null | head -1)
BEFORE_GL=$(ip route 2>/dev/null | grep 18.136 || echo "(khong co)")
BEFORE_DNS=$(getent ahostsv4 gitlab.simplemdg.com 2>/dev/null | head -1 | awk '{print $1}')
echo "    ra internet : $BEFORE_OUT"
echo "    route gitlab: $BEFORE_GL"
echo "    DNS gitlab  : ${BEFORE_DNS:-(khong giai duoc)}"
echo

echo "==> 1/3 Cai tailscale"
curl -fsSL https://tailscale.com/install.sh | sh

echo "==> 2/3 Bat dich vu"
systemctl enable --now tailscaled

echo "==> 3/3 Dang nhap"
echo "    Lenh duoi se in ra mot URL. Mo no bang BAT KY trinh duyet nao,"
echo "    tren bat ky may nao — khac voi luong SAML cua VPN, URL nay khong"
echo "    bat buoc phai o dung may nay."
echo
tailscale up --ssh --accept-routes=false --accept-dns=false

echo
echo "==> Kiem: dinh tuyen co bi Tailscale lam xe dich khong"
AFTER_OUT=$(ip route get 1.1.1.1 2>/dev/null | head -1)
AFTER_GL=$(ip route 2>/dev/null | grep 18.136 || echo "(khong co)")
AFTER_DNS=$(getent ahostsv4 gitlab.simplemdg.com 2>/dev/null | head -1 | awk '{print $1}')

fail=0
[ "$BEFORE_OUT" = "$AFTER_OUT" ] || { echo "    !! duong ra internet DA DOI"; fail=1; }
[ "$BEFORE_GL"  = "$AFTER_GL"  ] || { echo "    !! route gitlab DA DOI"; fail=1; }
[ "$BEFORE_DNS" = "$AFTER_DNS" ] || { echo "    !! DNS gitlab DA DOI"; fail=1; }
if [ "$fail" -eq 0 ]; then
  echo "    OK — ra internet, route gitlab va DNS deu giu nguyen"
else
  echo "    ra internet : $AFTER_OUT"
  echo "    route gitlab: $AFTER_GL"
  echo "    DNS gitlab  : ${AFTER_DNS:-(khong giai duoc)}"
  echo "    => xem lai truoc khi tin, worker co the bi anh huong"
fi

echo
echo "==> Dia chi Tailscale cua may nay"
tailscale ip -4 2>/dev/null | sed 's/^/    /'
tailscale status 2>/dev/null | head -5 | sed 's/^/    /'

cat <<'DONE'

================================================================
Xong. Con hai viec phai lam trong bang dieu khien Tailscale
(https://login.tailscale.com/admin/machines) — ca hai deu quan trong:

  1. TAT HAN KHOA cho may nay
     Machines -> may nay -> ... -> Disable key expiry

     Mac dinh khoa het han sau 180 ngay va may se RUNG khoi mang.
     Do la kieu hong lang le ma ban chi phat hien khi can toi no nhat.

  2. BAT MFA cho tai khoan Tailscale
     Tai khoan do gio la chia khoa vao mot may dang giu phien SSO
     cua cong ty (song 90 ngay). Xung dang bao ve bang MFA.

Tu laptop (da cai Tailscale, dang nhap cung tai khoan):

     tailscale status            # xem ten may
     ssh laidonit@<ten-may>      # khong can nho IP, khong can o nha

Ke tu day dia chi LAN cua may khong con quan trong nua — no doi tu
.205 sang .213 roi .254 trong mot buoi toi, moi lan la mot lan mat SSH.
Ten Tailscale thi khong doi.
================================================================
DONE
