# 01. Chuỗi Xuất Bản Sự Kiện (Event Publishing Chain)

> **Phân hệ:** AI Workflow Management  
> **Chủ đề:** Mẫu thiết kế Decorator Pattern 5 tầng trong xuất bản sự kiện, Write-Ahead Logging và phân cấp ưu tiên sự kiện.

---

## 1. Thiết Kế Chuỗi Decorator 5 Tầng

Mọi sự kiện phát sinh từ Engine hay Use Case đều đi qua một chuỗi các lớp xuất bản (Publishers) lồng vào nhau theo **Decorator Pattern**. Thiết kế này giúp phân tách hoàn toàn các mối quan tâm (Separation of Concerns) mà không làm ô nhiễm mã nguồn điều phối nghiệp vụ.

```
[OrchestrationEngine / UseCase]
              │
              ▼ gọi publish(topic, event)
┌──────────────────────────────────────────────────────────────┐
│ 1. EventLogAppendingPublisher                                │
│    - Ghi Write-Ahead Log (WAL) vào CSDL (Memory/HANA)        │
│    - Nếu ghi log lỗi -> Không làm tắc nghẽn luồng chính      │
└──────────────────────────────┬───────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────┐
│ 2. RealtimeBroadcastingPublisher                             │
│    - Chuẩn hóa topic sang format dấu chấm (vd: task.dispatch)│
│    - Xác định danh sách room: run:{id}, workflow:{id}        │
│    - Đẩy vào topic broker: "realtime.events"                 │
└──────────────────────────────┬───────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────┐
│ 3. EnrichingEventPublisher                                   │
│    - Gắn thêm root_run_id, workflow metadata, trace_id       │
│    - Chuyển tiếp tới hàng đợi sự kiện tập trung              │
└──────────────────────────────┬───────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────┐
│ 4. HttpTaskDispatcher / GrpcTaskDispatcher                   │
│    - Rẽ nhánh riêng cho sự kiện "task.dispatched"            │
│    - Bắn bất đồng bộ sang Worker Executor Service            │
└──────────────────────────────┬───────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────┐
│ 5. BackendPublisher (Lớp Cơ Sở)                              │
│    - ConsolePublisher: In stdout (Dev/Test)                  │
│    - KafkaPublisher: Ghi trực tiếp vào Apache Kafka broker   │
│    - EventMeshPublisher: Đẩy qua SAP Event Mesh HTTP/AMQP    │
└──────────────────────────────────────────────────────────────┘
```

---

## 2. Chi Tiết Nhiệm Vụ Của Từng Lớp

### Tầng 1: Write-Ahead Log (`EventLogAppendingPublisher`)
- **Nguyên tắc cốt lõi:** Bất kỳ sự kiện nào trước khi được loan báo ra bên ngoài mạng đều phải được ghi nhận vào kho lưu trữ bền vững (Event Log).
- Đóng gói sự kiện thành `EventLogEnvelope` chứa đầy đủ timestamp, payload dạng dict, và định danh nguồn phát.
- Cơ chế Shadow Log: Nếu việc lưu vào DB gặp sự cố, hệ thống ghi log cảnh báo nhưng không ném exception làm chết luồng điều phối chính.

### Tầng 2: Phát Realtime (`RealtimeBroadcastingPublisher`)
- Không gọi trực tiếp tới Socket.IO server trong tiến trình điều phối (tránh làm tăng độ trễ của Engine).
- Thay vào đó, nó tạo envelope `{ event_type, rooms, payload }` và gửi vào topic `realtime.events` của Message Broker với partition key là `root_run_id`.
- Đảm bảo tất cả sự kiện của cùng 1 run đi vào cùng 1 partition, giữ nguyên tuyệt đối thứ tự thời gian.

### Tầng 3: Bổ Sung Dữ Liệu Ngữ Cảnh (`EnrichingEventPublisher`)
- Đảm bảo các hệ thống bên thứ ba (Datalake, Auditing, Analytics) nhận được thông điệp hoàn chỉnh mà không cần phải truy vấn ngược lại database.
- Bổ sung: `tenant_id`, `root_run_id`, `execution_path`, `triggered_by`.

### Tầng 4: Rẽ Nhánh Dispatch Tác Vụ (`HttpTaskDispatcher`)
- Lắng nghe sự kiện `task.dispatched`.
- Nếu task này cần ủy thác cho worker ngoại vi (`worker_type` được thiết lập), dispatcher sẽ kích hoạt một HTTP request (chạy trên thread pool riêng biệt, non-blocking) để gửi sang Worker Executor.

### Tầng 5: Driver Hạ Tầng Cơ Sở (`BackendPublisher`)
- Cung cấp triển khai vật lý cuối cùng để ghi dữ liệu ra mạng: Kafka producer, SAP Event Mesh client, hoặc Console logging.

---

## 3. Phân Cấp Ưu Tiên Sự Kiện (`EventCollector`)

Để tránh hiện tượng sự kiện hoàn thành (`run.completed`) đến trước khi sự kiện kích hoạt tác vụ con (`task.dispatched`) được lưu, hệ thống sử dụng bộ đệm `EventCollector` với 3 mức độ ưu tiên:

```
Ưu Tiên 0 (IMMEDIATE) : task.dispatched, input.requested, child_run.started
         ▲
         │ Được drain và xuất bản trước
Ưu Tiên 1 (NORMAL)    : edge.traversed, task.completed, condition.evaluated
         ▲
         │ Được drain sau khi đã phát hết IMMEDIATE
Ưu Tiên 2 (TERMINAL)  : run.completed, run.failed, run.cancelled
```

Khi kết thúc một chu trình xử lý node (`_exec_uniform`), hàm `drain_events()` sẽ tự động sắp xếp các sự kiện tích lũy theo cặp `(priority, sequence_number)` trước khi đẩy xuống chuỗi Decorator.
