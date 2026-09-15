#!/usr/bin/env python3
"""Dua luong dang nhap SAML cua FortiGate qua Entra ID — khong can nguoi bam.

CACH HOAT DONG
  `openfortivpn --saml-login=8020` mo mot web server o 127.0.0.1:8020 roi ngoi
  doi. Script nay lai browser den <gateway>/remote/saml/start?redirect=1.
  Neu profile con phien Entra (cookie ESTSAUTHPERSISTENT — song 90 ngay cuon
  khi tick "Stay signed in"), Entra cho qua IM LANG va chuyen huong ve
  http://127.0.0.1:8020/?id=... ; openfortivpn nhat lay id do va dung tunnel.

  Vi vay chi phai dang nhap TAY DUNG MOT LAN:  saml_login.py --login

VI SAO KHONG TU DOC SVPNCOOKIE
  Doc cookie tu jar roi day qua --cookie-on-stdin cung chay, nhung phai tu doan
  ten cookie va thoi diem gateway set no. Dung dung listener co san cua
  openfortivpn thi it manh moc hon — day la duong ma chinh no thiet ke.

VI SAO KHONG CHAY BANG ROOT
  Chromium chay bang root vua khong duoc khuyen khich vua lam profile lech chu
  so huu. Orchestrator goi script nay qua `runuser -u <user>`.
"""

import argparse
import os
import re
import socket
import sys
import time

GATEWAY = os.environ.get("VPN_GATEWAY", "115.78.234.133:11443")
PORT = int(os.environ.get("VPN_SAML_PORT", "8020"))
PROFILE = os.environ.get(
    "VPN_PROFILE", os.path.expanduser("~/.local/share/fortivpn-saml/profile")
)
START_URL = f"https://{GATEWAY}/remote/saml/start?redirect=1"
REDIRECT_PREFIX = f"http://127.0.0.1:{PORT}"

# Rieng cho viec TAI TRANG gateway, tach khoi ngan sach ca luong SAML ben duoi.
# Ngay 17/08/2026 chinh cai nay het gio truoc, va viec gop hai thu lam mot da
# khien log ket luan sai la phien Entra het han.
NAV_TIMEOUT_MS = 45_000

# Ngan sach thu gon khi trang gateway KHONG tai duoc. Van cho mot khoang, vi
# goto() het gio khong huy dieu huong dang do — nhung khong cho het gio.
GRACE_S = 45

AADSTS_RE = re.compile("AADSTS[0-9]+")


def log(msg):
    print(f"[saml] {msg}", file=sys.stderr, flush=True)


def wait_for_listener(port, timeout):
    """Doi openfortivpn mo cong. Neu vao trang SAML truoc khi no san sang,
    chuyen huong cuoi se roi vao khoang khong va phai lam lai tu dau."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            s.settimeout(1)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.5)
    return False


def _one_line(exc):
    """Playwright nem loi nhieu dong; journal doc de hon voi mot dong."""
    return " ".join(str(exc).split())[:160]


def diagnose(page):
    """Doi trang thai cuoi cung thanh mot cau giai thich duoc.

    Chi ket luan "phien het han" khi CO BANG CHUNG, tuc mot ma AADSTS. Ban truoc
    suy ra dieu do chi tu viec URL nam o Entra — nhung mot gateway cham hoac
    khong voi toi duoc de lai dau vet Y HET, vi trinh duyet bi bo do giua duong.

    Ngay 17/08/2026 no da bao "phien het han, chay --login" trong khi phien con
    nguyen: gateway khong tai duoc trong ~7 phut, roi chu ky thu tu thanh cong
    trong 1 giay ma khong ai lam gi. Bao dong gia dat hon la khong bao gi.
    """
    url = page.url or ""
    try:
        body = page.content()
    except Exception:
        body = ""

    hit = AADSTS_RE.search(body) or AADSTS_RE.search(url)
    if hit:
        return (
            "Entra TU CHOI (%s) — phien het han hoac Conditional Access chan. "
            "Chay --login tren man hinh may nay." % hit.group(0)
        )

    if "login.microsoftonline.com" in url:
        return (
            "chua ra khoi trang Entra khi het gio, va KHONG thay ma AADSTS nao. "
            "Thuong la mang cham chu khong phai phien het han — dich vu se tu thu "
            "lai. Chi chay --login neu loi nay lap lai lien tuc."
        )

    if url in ("", "about:blank") or GATEWAY.split(":")[0] in url or "/remote/saml" in url:
        return (
            "khong tai duoc trang gateway (dung o %s) — gateway hoac mang khong voi "
            "toi duoc, KHONG phai van de phien dang nhap. Dich vu se tu thu lai."
            % (url or "about:blank")
        )

    return "dung o mot noi khong doan duoc: %s" % url


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--login",
        action="store_true",
        help="mo browser co giao dien de dang nhap tay lan dau "
        "(phai chay tu phien desktop cua may nay)",
    )
    ap.add_argument(
        "--timeout",
        type=int,
        default=0,
        help="giay cho luong SAML (mac dinh: 120 khi tu dong, 300 khi --login)",
    )
    args = ap.parse_args()
    timeout = args.timeout or (300 if args.login else 120)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("thieu playwright — chay lai setup-fortivpn-auto.sh")
        return 3

    os.makedirs(PROFILE, exist_ok=True)

    if args.login:
        if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            log("khong thay DISPLAY/WAYLAND_DISPLAY.")
            log("Lenh --login phai chay tu terminal TREN MAN HINH cua may nay,")
            log("khong chay qua SSH duoc.")
            return 4
        log("mo browser — dang nhap SSO va NHO TICK 'Stay signed in'")
        log("(do la thu sinh ra cookie 90 ngay; khong tick thi lan sau van hoi)")
    else:
        if not wait_for_listener(PORT, timeout=30):
            log(f"khong thay openfortivpn lang nghe o 127.0.0.1:{PORT}")
            return 5

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE,
            headless=not args.login,
            ignore_https_errors=True,  # gateway dung cert tu ky cua chinh no
            args=["--no-sandbox"] if os.geteuid() == 0 else [],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            # Co MOT truong hop goto() bao loi ma van la ket qua DUNG:
            # openfortivpn dong cong 8020 ngay sau khi nhan id, nen dieu huong
            # cuoi that bai o muc mang. Phan biet bang URL, khong bang gia dinh —
            # ban truoc ghi MOI loi goto la "thuong la binh thuong", nen mot
            # gateway khong voi toi duoc trong lot qua ma khong ai thay.
            nav_failed = False
            try:
                page.goto(
                    START_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS
                )
            except Exception as exc:
                if page.url.startswith(REDIRECT_PREFIX):
                    log("dieu huong ket thuc som o 127.0.0.1 — binh thuong")
                else:
                    nav_failed = True
                    log("KHONG tai duoc trang gateway: %s" % _one_line(exc))

            budget = GRACE_S if nav_failed else timeout
            deadline = time.time() + budget
            while time.time() < deadline:
                if page.url.startswith(REDIRECT_PREFIX):
                    log(f"SAML xong — openfortivpn da nhan id ({page.url[:60]}...)")
                    return 0
                time.sleep(1)

            log(f"het {budget}s ma chua ve 127.0.0.1: {diagnose(page)}")
            return 1
        finally:
            ctx.close()


if __name__ == "__main__":
    sys.exit(main())
