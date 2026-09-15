#!/usr/bin/env bash
#
# Cai openfortivpn (ban co SAML) + dinh tuyen CHIA DOI cho gitlab.simplemdg.com
# Chay: sudo bash setup-fortivpn-gitlab.sh    (chay lai nhieu lan duoc)
#
# --- VI SAO PHAI TU BUILD ---
# Ubuntu 24.04 chi co openfortivpn 1.21.0. SAML chi xuat hien tu 1.23.0.
# Gateway cua cong ty bat SSO (sso_enabled=1, use_external_browser=1) nen
# KHONG the dang nhap bang user/mat khau -> ban trong apt vo dung o day.
#
# --- VI SAO PHAI CHIA DOI TUYEN ---
# VPN cong ty la FULL TUNNEL (tren may Windows: 0.0.0.0/0 metric 1).
# Neu de nguyen, TOAN BO traffic cua claude-worker va log-worker se di qua
# VPN cong ty. Hai worker do chi noi chuyen voi cfapps.br10.hana.ondemand.com
# va vua on dinh lai hom qua — khong co ly do gi de day chung qua VPN.
# Script nay tat het route/DNS tu dong cua gateway va chi them DUNG route
# toi gitlab.simplemdg.com.

set -euo pipefail

GATEWAY_HOST=115.78.234.133
GATEWAY_PORT=11443
# sha256 cua cert TU KY tren chinh FortiGate (issuer FG100FTK21055288).
# He thong khong tin duoc chuoi nay (verify code 20) nen phai ghim tay.
GATEWAY_CERT=045958632baa7f1c05fca6f67f3e0b06cef8334761b1c8c8e3209ed2ceb6c458
TARGET_HOST=gitlab.simplemdg.com
SRC=/opt/openfortivpn-src

[ "$(id -u)" -eq 0 ] || { echo "Phai chay bang: sudo bash $0" >&2; exit 1; }

# openfortivpn khai bao `bin_PROGRAMS` nen no vao <prefix>/bin, KHONG phai
# sbin. Do la thu de doan nham, nen o day DO chu khong gia dinh.
find_vpnbin() {
  local p
  for p in /usr/local/bin/openfortivpn /usr/local/sbin/openfortivpn \
           /usr/bin/openfortivpn /usr/sbin/openfortivpn; do
    [ -x "$p" ] && { echo "$p"; return 0; }
  done
  command -v openfortivpn 2>/dev/null || true
}

has_saml() {
  [ -n "${1:-}" ] && [ -x "${1:-}" ] && "$1" --help 2>&1 | grep -q -- '--saml-login'
}

VPNBIN=$(find_vpnbin)

if has_saml "$VPNBIN"; then
  echo "==> Da co $VPNBIN ($("$VPNBIN" --version)) va no co --saml-login — bo qua buoc build"
else
  echo "==> 1/3 Cai do nghe build"
  apt-get update -qq
  apt-get install -y --no-install-recommends \
    build-essential autoconf automake pkg-config libssl-dev ppp git ca-certificates

  echo "==> 2/3 Lay ban moi nhat cua openfortivpn"
  if [ -d "$SRC/.git" ]; then
    git -C "$SRC" fetch --tags --quiet
  else
    rm -rf "$SRC"
    git clone --quiet https://github.com/adrienverge/openfortivpn.git "$SRC"
  fi
  cd "$SRC"
  TAG=$(git describe --tags --abbrev=0 origin/master)
  git checkout --quiet "$TAG"
  echo "    dung tag: $TAG"

  echo "==> 3/3 Build va cai vao /usr/local"
  ./autogen.sh >/dev/null 2>&1
  ./configure --prefix=/usr/local --sysconfdir=/etc >/dev/null
  make -j"$(nproc)" >/dev/null
  make install >/dev/null

  VPNBIN=$(find_vpnbin)
  [ -n "$VPNBIN" ] || { echo "LOI: build xong ma khong tim thay binary." >&2; exit 1; }
  printf "    da cai: %s (%s)\n" "$VPNBIN" "$("$VPNBIN" --version)"

  # Kiem SAML bang chinh binary, khong tin vao so phien ban.
  has_saml "$VPNBIN" || { echo "LOI: ban vua build khong co --saml-login." >&2; exit 1; }
  echo "    xac nhan: co --saml-login"
fi

echo "==> Ghi cau hinh"
install -d -m 0700 /etc/openfortivpn
cat > /etc/openfortivpn/gitlab.conf <<EOF
host = $GATEWAY_HOST
port = $GATEWAY_PORT
trusted-cert = $GATEWAY_CERT
# Khong nhan route va DNS tu gateway — xem ghi chu dau file setup.
set-routes = 0
set-dns = 0
pppd-use-peerdns = 0
EOF
chmod 0600 /etc/openfortivpn/gitlab.conf

echo "==> Cai hook them route khi tunnel len"
cat > /etc/ppp/ip-up.d/50-gitlab-split-route <<HOOK
#!/bin/sh
# pppd goi voi: \$1=interface  \$4=IP noi bo  \$5=IP peer  \$6=ipparam
# Chi hanh dong khi ipparam dung — de khong dung vao ppp khac.
[ "\$6" = "gitlab-vpn" ] || exit 0

# Giai ten NGAY LUC KET NOI thay vi ghim IP: gitlab nam tren AWS, IP co the doi.
for ip in \$(getent ahostsv4 $TARGET_HOST | awk '{print \$1}' | sort -u); do
  ip route replace "\$ip/32" dev "\$1"
  logger -t gitlab-vpn "them route \$ip/32 qua \$1"
done
HOOK
chmod 0755 /etc/ppp/ip-up.d/50-gitlab-split-route
# Route gan voi dev nen tu bien mat khi tunnel dong — khong can hook ip-down.

echo "==> Ghi lenh mo tunnel bang tay"
cat > /usr/local/bin/gitlab-vpn <<HELPER
#!/bin/sh
# Mo tunnel CHI cho $TARGET_HOST. Ban thu cong — de tu dong hoa hoan toan
# thi chay setup-fortivpn-auto.sh roi dung dich vu gitlab-vpn.service.
#
# Lenh nay se in ra mot URL. Mo URL do bang trinh duyet NGAY TREN MAY NAY
# (phien desktop o tty2), dang nhap nhu binh thuong. Cuoi luong trang se
# chuyen huong ve http://127.0.0.1:8020/ va tunnel se len.
#
# Mo tu may khac se KHONG duoc: 127.0.0.1 luc do la localhost cua may kia.
exec $VPNBIN \\
  -c /etc/openfortivpn/gitlab.conf \\
  --saml-login=8020 \\
  --pppd-ipparam=gitlab-vpn
HELPER
chmod 0755 /usr/local/bin/gitlab-vpn

cat <<DONE

================================================================
Xong phan cai. openfortivpn: $VPNBIN

Thu bang tay:
  sudo gitlab-vpn                  # in ra URL SAML, giu cua so nay chay
  # mo URL do bang Firefox TREN CHINH MAY NAY, dang nhap SSO

Hoac chay tiep giai doan 2 de no tu chay khong can bam:
  sudo bash ~/setup-fortivpn-auto.sh

Kiem sau khi tunnel len:
  ip route | grep ppp
  curl -sI https://$TARGET_HOST/ | head -1
  ip route get 1.1.1.1            # phai VAN qua wlp0s20f3, khong qua ppp0
  curl -s https://api.ipify.org   # phai VAN la IP nha
================================================================
DONE
