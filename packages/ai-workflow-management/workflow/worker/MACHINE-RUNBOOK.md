# Máy worker ở nhà — sổ tay vận hành

Máy Ubuntu chạy `claude-worker` và `log-worker` đã chuyển từ văn phòng về nhà.
Việc chuyển chỗ làm hỏng ba thứ cùng lúc, và tài liệu này ghi lại cách từng thứ
được nối lại — **cấu hình gì, đặt ở đâu, và vì sao chọn cách đó** thay vì cách
hiển nhiên hơn.

| Hỏng gì khi chuyển về nhà | Giải bằng | Chi tiết |
|---|---|---|
| Worker chết lặng, `docker ps` vẫn báo `Up` | healthcheck + systemd timer | [`systemd/README.md`](systemd/README.md) |
| Không vào được GitLab công ty | openfortivpn + SAML tự động, chia đôi tuyến | [`vpn/README.md`](vpn/README.md) |
| Không SSH về máy được từ bên ngoài | Tailscale | phần 3 dưới đây |

Hai file trên đi sâu vào từng phần. File này là **bức tranh toàn máy**: mạng,
kiểm kê, cách kiểm tra, và những gì còn cần đến con người.

---

## 0. Nhận dạng máy

| | |
|---|---|
| Hệ điều hành | Ubuntu 24.04.4 LTS |
| Đăng nhập | `laidonit@laidonit` (qua Tailscale) hoặc `laidonit@192.168.1.254` (trong nhà) |
| Giao diện mạng | `enx9c69d3ba9ad4` (Ethernet USB) — **chỉ một** |
| MAC | `9c:69:d3:ba:9a:d4` |
| Tên Tailscale | `laidonit` / `100.67.42.79` / `laidonit.tail82192a.ts.net` |
| Chạy gì | `claude-worker`, `log-worker` (Docker) |

`sudo` **cần mật khẩu**. Mọi bước cài trong tài liệu này phải do người vận hành
chạy; không có đường tự động hoá nào vòng qua chỗ đó.

---

## 1. Mạng

### Một giao diện, một địa chỉ

Trước đây máy có **hai** giao diện cùng nằm trên `192.168.1.0/24` — Wi-Fi
`.205` và Ethernet `.213`. Cả hai đều có default route, và hậu quả là traffic
**vào một đường, ra một đường khác**:

```
ssh vào:              192.168.1.205  (Wi-Fi)
ip route get 1.1.1.1: dev enx9c69d3ba9ad4 src 192.168.1.213  (Ethernet)
```

Trên cùng một segment L2 thì nó vẫn chạy, nhưng "ghim IP cố định" trở nên vô
nghĩa: có hai địa chỉ, và địa chỉ nào trả lời còn tuỳ đối phương hỏi qua đường
nào. Nên Wi-Fi đã được **tắt hẳn** (`rfkill`, `phy0 hard blocked`), giữ lại
Ethernet làm đường duy nhất.

### Địa chỉ cố định bằng reservation, không phải ghim tay

```
ipv4.method: auto
default via 192.168.1.1 dev enx9c69d3ba9ad4 proto dhcp src 192.168.1.254
```

Máy **vẫn chạy DHCP**; router `192.168.1.1` giữ chỗ `192.168.1.254` cho MAC
`9c:69:d3:ba:9a:d4`. Cách này hơn ghim tĩnh trên máy ở hai điểm: gateway/DNS
đổi thì máy tự theo, và không bao giờ đụng địa chỉ với thiết bị khác — vì chính
router là bên phát.

> Nếu ghim tĩnh trên máy mà không báo router, có ngày router cấp đúng địa chỉ đó
> cho thiết bị khác. Đó là kiểu lỗi chập chờn khó lần ra nhất.

Cách phân biệt tĩnh/động cho đúng: nhìn `ipv4.method` và `proto` trên default
route. **Đừng** nhìn cờ `dynamic`/`noprefixroute` trong `ip addr` — NetworkManager
đặt `noprefixroute` cho cả địa chỉ lấy từ DHCP.

### Ba địa chỉ không bao giờ được gán

Trong dải `/24`: `.0` (địa chỉ mạng), `.255` (broadcast), và địa chỉ router —
ở đây là `.1`. Gán `.1` cho máy làm sập mạng của **cả nhà**, không riêng máy đó,
vì mọi thiết bị khác sẽ lúc hỏi đúng router lúc hỏi nhầm, tuỳ ARP bên nào đáp
trước.

---

## 2. VPN → GitLab công ty

Chi tiết đầy đủ ở [`vpn/README.md`](vpn/README.md). Tóm tắt những gì đã đặt lên máy:

### Vì sao cần

`gitlab.simplemdg.com` chặn theo **IP nguồn**, không phải split-DNS. Tên miền
giải ra `18.136.29.109` từ bất cứ đâu, nhưng TCP chỉ mở cho IP đi ra của công ty.
Từ nhà là `HTTP 000`, cổng 22 cũng đóng.

### Vì sao phải chia đôi tuyến

VPN công ty là **full tunnel** (`0.0.0.0/0` metric 1). Để nguyên thì toàn bộ
traffic của hai worker — vốn chỉ nói chuyện với `cfapps.br10.hana.ondemand.com`
— sẽ đi vòng qua VPN công ty. Nên cấu hình đặt `set-routes = 0`, `set-dns = 0`,
rồi hook `ip-up` thêm **đúng một** route `/32` tới GitLab, giải tên ngay lúc kết
nối vì GitLab nằm trên AWS.

### Vì sao chạy được không người trực dù dùng SAML

Ba tầng hết hạn tách rời, chỉ tầng dưới cùng cần con người:

| Tầng | Hết hạn | Ai lo |
|---|---|---|
| Tunnel PPP | vài phút mất mạng | `systemd Restart=always` |
| `SVPNCOOKIE` | **8 giờ** (đo thật) | Chromium headless đi lại luồng SAML |
| Phiên Entra | 90 ngày cuộn (KMSI) | người — mỗi 90 ngày một lần |

### Số liệu vận hành thật

Đo trong 2 ngày đầu:

```
22:41:05 → 05:09:56    6h29m
05:23:15 → 13:23:47    8h00m
13:34:56 → 21:35:28    8h00m

12 lần tự hồi phục · 0 lần Entra đòi đăng nhập lại · 0 lần cần người
```

Hai khoảng đúng 8 giờ chẵn — `auth-timeout` mặc định của FortiGate (28800 giây).
Cookie hết hạn 3 lần mỗi ngày mà tầng dưới không hề bị đánh thức.

---

## 3. SSH từ bên ngoài — Tailscale

### Vì sao không mở port trên router

Nhà này nằm sau **CGNAT**:

```
1:  192.168.1.1        ← router nhà
2:  100.123.1.164      ← CGNAT của nhà mạng (dải 100.64.0.0/10)
3:  42.112.3.53
```

Router **không giữ** `118.69.12.194`; địa chỉ đó là của nhà mạng, chia cho nhiều
hộ. Không có cổng nào từ Internet dẫn tới đây để mà mở, và DDNS cũng vô nghĩa vì
tên miền sẽ trỏ về IP của nhà mạng.

**Kiểm điều này trước tiên** khi ai đó hỏi "mở SSH ra ngoài thế nào" — nó loại
luôn cách phổ biến nhất và tiết kiệm cả buổi loay hoay.

### Cài như thế nào

```bash
curl -fsSL https://tailscale.com/install.sh | sh
systemctl enable --now tailscaled
tailscale up --ssh --accept-routes=false --accept-dns=false
```

Hai cờ `false` là **có chủ ý**: cả hai đều có thể chèn route hoặc viết lại
`/etc/resolv.conf`, kéo traffic worker đi đường khác — đúng thứ đã mất công
tránh ở phần 2. Chiều cần ở đây chỉ là **ngoài vào**; không cần chiều ngược lại.

Script `~/setup-tailscale.sh` chụp lại định tuyến trước khi cài rồi so lại sau,
và báo nếu có gì xê dịch.

### Vì sao nó xuyên được CGNAT

`tailscale netcheck` cho ba đường, xếp theo thứ tự ưu tiên:

```
IPv6:                  yes, [2405:4802:8011:c990:...]:52833
IPv4:                  yes, 118.69.12.194:33157
MappingVariesByDestIP: true
PortMapping:           UPnP
Nearest DERP:          Singapore, 47.6ms
```

1. **IPv6 gốc** — nhà mạng cấp IPv6 thật, không qua CGNAT. Không có NAT nên đi thẳng.
2. **UPnP** — IPv4 khó vì `MappingVariesByDestIP: true` (NAT cấp cổng khác nhau
   cho mỗi đích, kiểu khó xuyên nhất), nhưng router mở được ánh xạ cổng để bù.
3. **DERP Singapore** — luôn chạy, không phụ thuộc NAT. 48ms, thừa cho SSH.

### Dùng

```bash
ssh laidonit@laidonit          # từ bất cứ đâu, máy kia cũng phải ở trong tailnet
```

### Hạn khoá — đã tắt

Mặc định khoá node hết hạn sau 180 ngày và máy sẽ **lặng lẽ rụng** khỏi tailnet,
phát hiện ra đúng lúc đang ở xa cần dùng. Đã tắt trong bảng điều khiển
(Machines → `laidonit` → Disable key expiry). Xác nhận:

```bash
tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"].get("KeyExpiry","(da tat)"))'
```

---

## 4. Sức khoẻ worker

Chi tiết ở [`systemd/README.md`](systemd/README.md). Điểm cốt lõi: **Docker không
tự dựng lại container `unhealthy`**. `restart:` kích hoạt khi tiến trình *thoát*;
`HEALTHCHECK` chỉ ghi một nhãn và không kèm hành động. Hai cơ chế đó không bao
giờ gặp nhau — và đúng khoảng trống ấy là chỗ sự cố 18 giờ lọt qua.

Ba lớp, mỗi lớp bắt một dạng hỏng khác nhau:

| Lớp | Bắt gì |
|---|---|
| `pull_multi` bounded wait | worker treo mà vẫn phải heartbeat được |
| `HEALTHCHECK` trong compose | container này còn heartbeat tươi không |
| `worker-autoheal.timer` | dựng lại container bị đánh dấu `unhealthy` |

> Autoheal dựng lại thường xuyên là **triệu chứng, không phải thành công**.
> `journalctl -u worker-autoheal.service` cho biết dựng lại lúc nào.

---

## 5. Kiểm kê — mọi thứ đã đặt lên máy

### Chương trình

| Đường dẫn | Là gì |
|---|---|
| `/usr/local/bin/openfortivpn` | v1.24.1, **build từ nguồn** — apt chỉ có 1.21.0, mà SAML cần ≥1.23.0 |
| `/opt/openfortivpn-src/` | cây nguồn để build lại |
| `/opt/fortivpn-saml/venv/` | Python venv có Playwright |
| `~/.cache/ms-playwright/` | Chromium + headless shell |
| `tailscale` | v1.102.2, từ kho của Tailscale |

> openfortivpn khai báo `bin_PROGRAMS` nên nó cài vào `/usr/local/**bin**`,
> **không phải `sbin`**. Đây là con bug đã nuốt lần cài đầu tiên; cả hai script
> giờ **dò** qua bốn vị trí thay vì đoán.

### File cấu hình

| Đường dẫn | Quyền | Là gì |
|---|---|---|
| `/etc/openfortivpn/gitlab.conf` | `0600` trong thư mục `0700` | host, port, `trusted-cert`, `set-routes=0`, `set-dns=0` |
| `/etc/ppp/ip-up.d/50-gitlab-split-route` | `0755` | thêm route `/32` tới GitLab khi tunnel lên |
| `/usr/local/bin/gitlab-vpn` | `0755` | mở tunnel **thủ công** (in URL SAML ra để người bấm) |
| `/usr/local/bin/gitlab-vpn-auto` | `0755` | orchestrator: openfortivpn + Playwright |
| `/opt/fortivpn-saml/saml_login.py` | `0755` | trình lái luồng SAML |
| `/etc/systemd/system/gitlab-vpn.service` | `0644` | dịch vụ tunnel |
| `/etc/systemd/system/worker-autoheal.{service,timer}` | `0644` | dựng lại container `unhealthy` |

Thư mục `/etc/openfortivpn` là `0700` của root, nên **không stat được bằng quyền
thường**. Đừng kết luận "file không tồn tại" từ một lệnh chạy không có sudo —
kiểm gián tiếp bằng việc tunnel có nối đúng gateway không.

### Dữ liệu trạng thái

| Đường dẫn | Là gì |
|---|---|
| `~/.local/share/fortivpn-saml/profile` | **phiên SSO công ty sống 90 ngày** (~5.8 MB) |
| `/var/lib/tailscale/` | trạng thái node Tailscale |

### Dịch vụ

```
gitlab-vpn.service      enabled  active
tailscaled.service      enabled  active
worker-autoheal.timer   enabled  active
docker.service          enabled  active
```

### Script cài (bản gốc trong repo)

| Trên máy | Trong repo |
|---|---|
| `~/setup-fortivpn-gitlab.sh` | [`vpn/setup-fortivpn-gitlab.sh`](vpn/setup-fortivpn-gitlab.sh) |
| `~/setup-fortivpn-auto.sh` | [`vpn/setup-fortivpn-auto.sh`](vpn/setup-fortivpn-auto.sh) |
| `~/setup-tailscale.sh` | [`setup-tailscale.sh`](setup-tailscale.sh) |

---

## 6. Kiểm tra nhanh

Dán nguyên khối này khi nghi có gì đó sai:

```bash
# Mạng
ip -br -4 addr show | grep -vE '^(lo|docker|br-|veth)'
ip route | grep '^default'

# VPN
systemctl is-active gitlab-vpn.service
ip route | grep 18.136                                  # phải có, dev ppp0
curl -sI https://gitlab.simplemdg.com/api/v4/version     # 401 = thông

# Worker KHÔNG bị kéo qua VPN  ← quan trọng nhất
ip route get 1.1.1.1                                     # phải KHÔNG qua ppp0
curl -s https://api.ipify.org                            # phải là IP nhà

# Worker
docker ps --format '{{.Names}}\t{{.Status}}' | grep -E 'claude-worker|log-worker'

# Tailscale
tailscale status
```

---

## 7. Sự cố thường gặp

| Triệu chứng | Nguyên nhân | Chữa |
|---|---|---|
| `[saml] van con dung o trang Entra` | phiên Entra 90 ngày hết hạn | chạy `saml_login.py --login` **trước màn hình máy** |
| GitLab `HTTP 000`, `ip route` không có `18.136` | tunnel chưa lên | `journalctl -u gitlab-vpn.service -n 50` |
| `ERROR: Bad request` / `Broken pipe` trong log VPN | Chromium bắn request phụ tới `127.0.0.1:8020` | **vô hại** — request thật đi lọt ngay sau đó |
| Worker `unhealthy` rồi được dựng lại | pull loop treo | xem log worker, không chỉ log autoheal |
| SSH `.254` timeout | mất điện / đổi mạng | dùng `ssh laidonit@laidonit` qua Tailscale |
| Không SSH được cả hai đường | máy tắt hoặc mất mạng nhà | cần người tới tận nơi |
| Tunnel lên rồi rớt sau đúng 8 tiếng | `auth-timeout` của FortiGate | **bình thường** — nó tự nối lại |

Hai chi tiết trong `gitlab-vpn.service` đừng sửa nếu chưa đọc:

- **`Restart=always`, không phải `on-failure`.** Khi `auth-timeout` hết,
  openfortivpn thoát **sạch với mã 0**; `on-failure` sẽ bỏ qua và tunnel nằm chết.
- **`StartLimitBurst=5` trong 30 phút.** Nếu Entra bắt đầu hỏi lại, dịch vụ dừng
  hẳn thay vì quay điên và trôi mất log.

---

## 8. Những gì còn cần đến con người

| Việc | Tần suất | Làm ở đâu |
|---|---|---|
| Đăng nhập lại SSO cho VPN | ~90 ngày | **trước màn hình máy** — không qua SSH được |
| Mọi thứ khác | — | không |

Bước seed lại bắt buộc phải ở đúng máy đó vì luồng SAML chuyển hướng về
`127.0.0.1:8020`; mở URL từ máy khác thì `127.0.0.1` là localhost của máy kia.

*(URL đăng nhập của Tailscale thì ngược lại — mở từ máy nào cũng được.)*

---

## 9. Máy này đang giữ gì

Xếp theo mức nhạy cảm giảm dần:

1. **`~/.local/share/fortivpn-saml/profile`** — phiên SSO công ty sống 90 ngày.
   Nhạy cảm hơn bất kỳ token nào ở đây. Nếu máy thất lạc, thứ cần thu hồi **trước
   tiên** là phiên Entra, không phải cái repo.
2. **Tài khoản Tailscale** — giờ là cửa duy nhất vào máy này từ bên ngoài. Nếu là
   tài khoản cá nhân thì càng nên bật MFA; lớp bảo vệ của cả chuỗi mỏng đúng bằng
   lớp bảo vệ của tài khoản ấy.
3. `~/.git-credentials` — token GitHub.

Nên cho IT biết máy này đang giữ một phiên SSO như vậy. Không phải vì nó sai —
đây là tự động hoá quyền truy cập của chính người dùng — mà vì phạm vi ảnh hưởng
rộng hơn những gì tên thư mục gợi ý.

---

## 10. Đường lui, nếu chỉ cần kéo code

Toàn bộ phần VPN tồn tại để `git pull` từ GitLab công ty. Nếu đó là nhu cầu duy
nhất thì có đường ngắn hơn nhiều: GitLab có **push mirror** sẵn trong bản Free
(Settings → Repository → Mirroring repositories), đẩy sang GitHub trong 1–5 phút.
Máy này vốn đã pull GitHub ngon lành mà không cần VPN.

Không có gì phải giữ sống, không phiên nào hết hạn, không cần cài gì lên máy.
Đổi lại: một chiều, và chỉ đồng bộ repo — không có container registry, API hay
CI của GitLab.
