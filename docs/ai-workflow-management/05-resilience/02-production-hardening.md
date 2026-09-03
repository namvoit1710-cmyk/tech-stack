# 02. Tính Bền Vững & Tối Ưu Hóa Enterprise (Production Hardening)

> **Phân hệ:** AI Workflow Management  
> **Chủ đề:** Khóa lạc quan CAS retry, Transactional Outbox Pattern, các Janitor dọn dẹp bộ nhớ và cơ chế Durable Timers.

---

## 1. Khóa Lạc Quan & Thử Lại CAS (`save_run_with_cas_retry`)

Trong môi trường phân tán nơi nhiều worker có thể hoàn thành các nhánh song song và cùng lúc ghi kết quả vào trạng thái `Run`:
- Hệ thống áp dụng **Optimistic Concurrency Control (OCC)** thông qua cột `version` trong bảng dữ liệu SAP HANA:
  ```sql
  UPDATE WF_RUNS 
  SET STATUS = :status, CONTEXT = :context, VERSION = VERSION + 1
  WHERE ID = :run_id AND VERSION = :expected_version
  ```
- Nếu 2 worker cùng ghi đồng thời, một bên sẽ thất bại vì `version` đã thay đổi.
- Hàm `save_run_with_cas_retry` (`layer2_application/run_save_retry.py`) sẽ tự động:
  1. Đọc lại bản ghi `Run` mới nhất từ Database.
  2. Hòa trộn (merge) các biến mới vào ngữ cảnh vừa đọc.
  3. Thử lưu lại với số lần retry tối đa (mặc định 5 lần) kết hợp thuật toán lùi lũy thừa (exponential backoff).

---

## 2. Đảm Bảo Tính Nhất Quán: Transactional Outbox Pattern

Khi Control Plane cần vừa cập nhật trạng thái vào Database vừa xuất bản sự kiện lên Kafka:
- Nếu ghi DB thành công nhưng mạng Kafka lỗi ➔ Mất sự kiện, workflow bị treo.
- Nếu gửi Kafka trước nhưng ghi DB lỗi ➔ Sự kiện ma được phát tán ra ngoài.

### Giải pháp Outbox Pattern (Phase 3 Architecture):
1. Trong cùng một transaction SQL với bảng `WF_RUNS`, engine ghi bản tin sự kiện vào bảng trung gian `WF_OUTBOX`.
2. Một tiến trình chạy ngầm độc lập (`OutboxDispatcher`) liên tục quét bảng `WF_OUTBOX`:
   - Đọc các event chưa gửi.
   - Gửi lên Apache Kafka / SAP Event Mesh.
   - Đánh dấu đã gửi thành công hoặc xóa khỏi bảng outbox.
3. **Đảm bảo tính chất:** *At-least-once delivery* — không bao giờ bị mất sự kiện dù dịch vụ có bị crash đột ngột.

---

## 3. Các Tiến Trình Dọn Dẹp Tài Nguyên (Janitors & Resource Collectors)

Để dịch vụ Python có thể chạy ổn định 24/7 trên môi trường Kubernetes / Cloud Foundry mà không bị crash do rò rỉ bộ nhớ (OOMKilled):

### 3.1. `MallocTrimJanitor` (Thu Hồi Bộ Nhớ glibc)
- **Vấn đề của Python trên Linux:** Trình quản lý bộ nhớ của glibc thường giữ lại các arena bộ nhớ đã được Python giải phóng thay vì trả về cho hệ điều hành. Điều này khiến pod hiển thị mức sử dụng RAM cao liên tục.
- **Cơ chế:** Định kỳ quét nếu dung lượng bộ nhớ rảnh trong arena vượt ngưỡng (ví dụ > 64MB), tiến trình sẽ kích hoạt lệnh `malloc_trim(0)`, ép trả toàn bộ RAM nhàn rỗi về cho Kernel OS.

### 3.2. `EventLoopLagMonitor` (Giám Sát Nghẽn Event Loop)
- Do Python Asyncio là đơn luồng (single-threaded event loop), nếu có đoạn mã nào gọi đồng bộ chặn luồng (blocking I/O hoặc tính toán nặng), toàn bộ các request khác sẽ bị đơ.
- `EventLoopLagMonitor` liên tục đo thời gian trễ thực tế giữa các nhịp tick của event loop và gửi cảnh báo Prometheus nếu độ trễ vượt quá ngưỡng an toàn (ví dụ > 500ms).

### 3.3. `CacheExpiryJanitor` (Dọn Dẹp Thông Tin Xác Thực)
- Dọn dẹp các bản ghi nhạy cảm (như mật khẩu, API keys, decrypted credentials) lưu trong bộ nhớ đệm `BoundedTTLCache` sau khi phiên chạy kết thúc, ngăn chặn nguy cơ lộ dữ liệu trong heap memory.

---

## 4. Bộ Hẹn Giờ Bền Vững & Lập Lịch Cron (Durable Timers)

- Hệ thống cung cấp dịch vụ `TimerDispatcher` kết hợp lưu trữ cơ sở dữ liệu:
  - Khi node `WAIT` yêu cầu chờ 3 tiếng, một bản ghi timer được tạo trong DB với trạng thái `PENDING`.
  - Nếu pod máy chủ bị khởi động lại giữa chừng, sau khi bật lại `TimerDispatcher` sẽ tự động quét các timer đến hạn trong DB và bắn sự kiện `timer.fired` lên topic để tiếp tục chạy workflow.
- **Lập lịch Cron (`fire_due_schedules`):** Quét các workflow có cấu hình Trigger dạng Schedule/Crontab và kích hoạt các Run mới theo đúng chu kỳ đặt trước.
