# Tunnel tới GitLab công ty — chạy 24/7, không người trực

Máy chạy `claude-worker` / `log-worker` đã chuyển về nhà. Từ đó nó **không vào
được GitLab công ty** nữa, nên không `git pull` được.

## Vì sao không vào được

`gitlab.simplemdg.com` chặn theo **IP nguồn**, không phải split-DNS:

| | Trong mạng công ty | Ở nhà |
|---|---|---|
| DNS | 18.136.29.109 | 18.136.29.109 — **giống hệt** |
| HTTPS | nginx 302 → `/users/sign_in` | không kết nối được (HTTP 000) |
| Cổng 22 | mở | đóng |

Cùng một IP đích, DNS giải bình thường từ nhà. Khác biệt duy nhất là IP đi ra.
Nên việc cần làm là cho traffic tới GitLab đi qua VPN — và **chỉ** traffic đó.

## Vì sao phải chia đôi tuyến

VPN của công ty là **full tunnel** — nó đẩy `0.0.0.0/0` với metric 1.

Để nguyên thì toàn bộ traffic của `claude-worker` và `log-worker` sẽ đi qua VPN
công ty. Hai worker đó chỉ nói chuyện với `cfapps.br10.hana.ondemand.com`;
không có lý do gì đẩy chúng qua đó, và làm vậy là tự tay đưa chúng trở lại đúng
vùng rủi ro mà [`../systemd/README.md`](../systemd/README.md) vừa xử lý xong.

Nên `gitlab.conf` đặt `set-routes = 0` và `set-dns = 0`, rồi hook
`/etc/ppp/ip-up.d/50-gitlab-split-route` tự thêm đúng một route `/32` tới
GitLab. Hook giải tên **ngay lúc kết nối** thay vì ghim IP, vì GitLab nằm trên
AWS và địa chỉ có thể đổi.

## Vì sao chạy được không người trực, dù dùng SAML

"Đăng nhập SAML" không phải một hành động liền khối. Nó là ba tầng hết hạn
chồng lên nhau, và **chỉ tầng dưới cùng mới cần con người**:

| Tầng | Hết hạn sau | Ai lo |
|---|---|---|
| Tunnel PPP | vài phút mất mạng | `systemd Restart=always` |
| `SVPNCOOKIE` (FortiGate) | `auth-timeout` ≤ 3 ngày | Chromium headless đi lại luồng SAML |
| Phiên Entra (`ESTSAUTHPERSISTENT`) | **90 ngày cuộn** (KMSI) | người — mỗi 90 ngày một lần |

Vì vậy chỉ phải đăng nhập tay một lần, rồi 90 ngày sau mới phải làm lại.

## Cài

```bash
sudo bash setup-fortivpn-gitlab.sh   # build openfortivpn + config + hook route
sudo bash setup-fortivpn-auto.sh     # Playwright + orchestrator + systemd unit
```

Giai đoạn 1 **phải tự build**: Ubuntu 24.04 chỉ có openfortivpn 1.21.0, mà SAML
chỉ xuất hiện từ 1.23.0. Nó khai báo `bin_PROGRAMS` nên cài vào
`/usr/local/bin/`, **không phải `sbin`** — cả hai script đều dò thay vì đoán.

Chạy lại nhiều lần được: nếu binary đã có `--saml-login` thì bước build tự bỏ qua.

## Bước tay duy nhất

Phải làm **trước màn hình máy đó**, không qua SSH được:

```bash
/opt/fortivpn-saml/venv/bin/python /opt/fortivpn-saml/saml_login.py --login
```

Chromium mở ra, đăng nhập SSO, và **nhớ tick "Stay signed in"** — đó chính là
thứ sinh ra cookie 90 ngày. Xong thì:

```bash
sudo systemctl enable --now gitlab-vpn.service
```

## Kiểm

```bash
systemctl status gitlab-vpn.service
ip route | grep ppp                       # phải có 18.136.29.109 dev ppp0
curl -sI https://gitlab.simplemdg.com/api/v4/version   # 401 = thông
```

Quan trọng nhất — kiểm worker **không** bị kéo qua VPN:

```bash
ip route get 1.1.1.1            # phải KHÔNG qua ppp0
curl -s https://api.ipify.org   # phải vẫn là IP nhà
```

Thử khả năng tự hồi phục:

```bash
sudo pkill -f 'openfortivpn -c /etc/openfortivpn/gitlab.conf'
journalctl -u gitlab-vpn.service -f       # ~30 giây sau phải tự lên lại
```

Đã kiểm 15/08/2026: hồi phục trong ~31 giây, luồng SAML tự đi qua Entra không
hỏi gì. Tính tới lúc ghi file này nó đã tự đứng dậy 3 lần, trong đó 2 lần là sự
cố thật (mạng chuyển Wi-Fi → Ethernet, và đổi IP) chứ không phải thử nghiệm.

## Hai chi tiết trong unit, đừng sửa nếu chưa đọc

**`Restart=always`, không phải `on-failure`.** Khi FortiGate hết `auth-timeout`,
openfortivpn thoát **sạch với mã 0**. `on-failure` sẽ bỏ qua và tunnel nằm chết.

**`StartLimitBurst=5` trong 30 phút.** Nếu Entra bắt đầu hỏi lại, dịch vụ dừng
hẳn thay vì quay điên và trôi mất log.

## Dòng `ERROR: Bad request` trong log

Xuất hiện ở đầu mỗi chu kỳ rồi tự khỏi sau một giây. Chromium bắn một request
phụ tới `127.0.0.1:8020` mà máy chủ SAML tí hon của openfortivpn không hiểu;
request thật ngay sau đó đi lọt. Vô hại.

## Thứ nhạy cảm nhất ở đây

`~/.local/share/fortivpn-saml/profile` giữ một **phiên SSO công ty sống 90 ngày**.
Nó nhạy cảm hơn cả token GitLab. Nếu máy thất lạc, thứ cần thu hồi là phiên
Entra, không phải cái repo.

Nên cho IT biết máy này đang giữ một phiên như vậy — không phải vì nó sai, mà vì
phạm vi ảnh hưởng rộng hơn những gì tên thư mục gợi ý.

## Khi hết 90 ngày

Log sẽ hiện `[saml] van con dung o trang Entra`. Chữa bằng đúng lệnh
`saml_login.py --login` ở trên, chạy lại trước màn hình máy.

## Đường vòng nếu chỉ cần kéo code

GitLab có **push mirror** sẵn trong bản Free (Settings → Repository → Mirroring
repositories), đẩy sang GitHub trong 1–5 phút. Máy này vốn đã pull GitHub ngon
lành mà không cần VPN. Không có gì phải giữ sống, không có phiên nào hết hạn —
đổi lại là một chiều và chỉ đồng bộ repo, không có registry/API/CI.
