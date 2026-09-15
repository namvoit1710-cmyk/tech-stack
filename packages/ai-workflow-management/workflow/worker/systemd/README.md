# worker-autoheal — dựng lại container `unhealthy`

## Vì sao cần

Docker **không** làm việc này. Hai cơ chế nghe như liên quan nhưng không gặp nhau:

| | Kích hoạt bởi | Không quan tâm |
|---|---|---|
| `restart: unless-stopped` | tiến trình **thoát** (exit code) | container `unhealthy` |
| `HEALTHCHECK` | ghi nhãn `healthy` / `unhealthy` | không có hành động đi kèm |

Swarm và Kubernetes có xử lý `unhealthy`. Docker Engine trên một máy đơn thì không
— không có bộ điều phối nào đọc nhãn đó để hành động.

Đúng khoảng trống ấy là chỗ sự cố **14/08/2026** lọt qua: vòng lặp pull của cả
`claude-worker` lẫn `log-worker` treo sau một cú chớp DNS, tiến trình **không
thoát**, nên `restart:` không kích hoạt. Cả hai im lặng ~18 giờ trong khi
`docker ps` báo `Up` suốt thời gian đó.

## Cài (cần sudo)

```bash
W=/home/laidonit/apps/simplemdg_ai/apps/backend/ai-workflow-management/workflow/worker
sudo cp $W/systemd/worker-autoheal.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now worker-autoheal.timer
```

## Kiểm

```bash
systemctl status worker-autoheal.timer      # phải "active (waiting)"
systemctl list-timers worker-autoheal.timer # lần chạy kế tiếp
journalctl -u worker-autoheal.service -n 20 # lịch sử dựng lại
```

Thử thật — ép một container thành `unhealthy` rồi xem nó có được dựng lại trong
~30 giây không:

```bash
docker pause log-worker      # healthcheck không exec được → unhealthy
watch -n5 'docker ps --format "{{.Names}}\t{{.Status}}" | grep log-worker'
```

## Cái này thay cho gì

Trước đây dùng container `willfarrell/autoheal`. Nó chạy tốt (đã kiểm 14/08: phát
hiện và dựng lại trong 26 giây), nhưng phải mount `/var/run/docker.sock` — **tương
đương quyền root trên máy**, cho một tiến trình chạy thường trực.

Timer này làm đúng việc đó bằng năm dòng shell, không có tiến trình dài hạn nào
cầm docker socket, và chạy dưới quyền systemd chứ không phải trong một image bên
thứ ba cần theo dõi cập nhật.

## Lọc theo nhãn, không quét tất cả

Chỉ những container mang `autoheal: "true"` trong compose mới bị đụng tới. Đặt
nhãn là hành động **có ý**, nên một container mới thêm vào máy sẽ không bị dựng
lại ngoài ý muốn.

Hiện đang mang nhãn: `claude-worker`, `log-worker`.

## Nếu thấy nó dựng lại thường xuyên

Đó là **triệu chứng**, không phải thành công. `journalctl -u worker-autoheal.service`
cho biết dựng lại lúc nào; nhiều lần trong ngày nghĩa là có gì đó làm worker treo
lặp lại và cần tìm nguyên nhân, chứ không phải để cơ chế này che đi.
