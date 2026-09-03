# 03. Kiến Trúc Phân Tầng Clean Architecture

> **Phân hệ:** AI Workflow Management  
> **Chủ đề:** Chi tiết 4 tầng Clean Architecture, các Entities, Use Cases, Adapters và Frameworks.

---

## 1. Nguyên Tắc Phụ Thuộc (The Dependency Rule)

Hệ thống tuân thủ chặt chẽ mô hình Clean Architecture gồm 4 tầng hình tròn đồng tâm:
- **Tầng trong** không được biết bất cứ thông tin gì về **tầng ngoài**.
- Mọi phụ thuộc giữa các tầng đều hướng từ ngoài vào trong.
- Lớp Domain và Application hoàn toàn độc lập với các thư viện bên ngoài như FastAPI, SQLAlchemy, hdbcli, Kafka hay Redis.

```
┌─────────────────────────────────────────────────────────────┐
│  Layer 4: Frameworks & Drivers (HANA, Kafka, Redis, FastAPI)│
│  ┌────────────────────────────────────────────────────────┐ │
│  │  Layer 3: Interface Adapters (REST, gRPC, Consumers)   │ │
│  │  ┌──────────────────────────────────────────────────┐  │ │
│  │  │  Layer 2: Application Core (Use Cases, Engine)   │  │ │
│  │  │  ┌────────────────────────────────────────────┐  │  │ │
│  │  │  │  Layer 1: Domain Core (Entities, Events)   │  │  │ │
│  │  │  └────────────────────────────────────────────┘  │  │ │
│  │  └──────────────────────────────────────────────────┘  │ │
│  └────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. Chi Tiết Các Tầng (Layers Breakdown)

### 2.1. Layer 1: Domain Core (`app/layer1_domain`)

Là trái tim của hệ thống, chứa các quy tắc nghiệp vụ cốt lõi không thay đổi theo thời gian:

- **Thực thể (Entities):**
  - `Workflow`: Định nghĩa cấu trúc luồng công việc, danh sách node và các cạnh nối (`wires`).
  - `Run`: Thể hiện một phiên thực thi của workflow, chứa trạng thái tổng (`RunStatus`), phiên bản gắn chặt (`workflow_version`), context biến (`run.context.variables`).
  - `Task`: Đơn vị thực thi của một node trong phiên chạy cụ thể, chứa trạng thái (`TaskStatus`), input đã phân giải, output thu được và `scope_stack`.
  - `EventLogEnvelope`: Gói tin sự kiện bất biến lưu trữ vào Event Log.
- **Đối tượng giá trị (Value Objects):**
  - `NodeType`: Enum 16 loại node (`TRIGGER`, `TASK`, `CONDITION`, `SWITCH`, `LOOP`, `PARALLEL`, `MERGE`, v.v.).
  - `TaskStatus`: `PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, `SKIPPED`.
  - `Port`: Đại diện cho các cổng kết nối dữ liệu (`main`, `error`, `true`, `false`, `default`).
- **Sự kiện nghiệp vụ (Domain Events):**
  - `RunStarted`, `RunCompleted`, `RunFailed`, `RunCancelled`.
  - `TaskDispatched`, `TaskCompleted`, `TaskFailed`, `TaskSkipped`, `InputRequested`.
  - `EdgeTraversed`, `NodeOutputProduced`, `LoopIterationCompleted`.
- **Hàm nghiệp vụ thuần túy (Pure Functions):**
  - `compute_fanout()`: Thuật toán xác định các node kế tiếp, đánh giá điều kiện rẽ nhánh và kích hoạt Cascade-Skip mà không có bất kỳ side-effect nào ra ngoài.
  - `apply_event()`: Áp dụng một sự kiện vào RunState để tái tạo trạng thái mới.

---

### 2.2. Layer 2: Application Core (`app/layer2_application`)

Chứa các kịch bản sử dụng (Use Cases) và bộ máy điều phối:

- **Bộ máy điều phối (Orchestration Engine):**
  - `OrchestrationEngine`: Đóng vai trò là façade phối hợp, cung cấp các hàm nghiệp vụ chính: `orchestrate_start()`, `orchestrate_continue()`, `orchestrate_execute_node()`, `orchestrate_loop_iteration()`.
  - `NodeExecutionRunner`: Chuẩn bị ngữ cảnh, nạp biến vòng lặp, kiểm tra tiền nhiệm và kích hoạt handler.
  - `SuccessorProcessor`: Nhận kết quả từ node, phối hợp cùng `compute_fanout()` để sinh các task kế tiếp.
  - `TaskFactory`: Sinh ra thực thể `Task` kèm theo ngữ cảnh thừa kế scope (`scope_stack`).
- **Tập hợp 16 Node Handlers:**
  - Kế thừa giao diện chung `NodeHandler`. Mỗi handler xử lý logic chuyên biệt của node type tương ứng và trả về `NodeHandlerResult` kèm theo `completion_mode` (`immediate`, `dispatched`, `pending`, ...).
- **Hơn 90 Use Cases chuyên biệt:**
  - Được tổ chức theo thư mục tính năng (`features/`), mỗi Use Case chỉ làm đúng một nhiệm vụ duy nhất (Single Responsibility Principle): `StartRunUseCase`, `CompleteTaskUseCase`, `CancelRunUseCase`, `EditNodeDataUseCase`, `SyncGatewayAppOpenAPIUseCase`...

---

### 2.3. Layer 3: Interface Adapters (`app/layer3_adapters`)

Chuyển đổi dữ liệu giữa định dạng thuận tiện cho use case và định dạng của các tác nhân bên ngoài:

- **REST Controllers (FastAPI):**
  - Được phân tách theo domain tài nguyên: `/api/v1/workflows`, `/api/v1/runs`, `/api/v1/tasks`, `/api/v1/nodes`.
  - Sử dụng DTOs (Data Transfer Objects) và Pydantic schemas để validate request đầu vào và serialize response đầu ra.
- **gRPC Servicers:**
  - Hiện thực hóa các RPC interface định nghĩa trong file `.proto`: `TaskDispatchServiceServicer`, `PushGatewayServiceServicer`.
- **Event Consumers & Action Routers:**
  - Nhận action từ Orchestration Queue và điều hướng vào đúng phương thức của `OrchestrationEngine`.

---

### 2.4. Layer 4: Frameworks & Drivers (`app/layer4_frameworks`)

Tầng ngoài cùng chứa tất cả chi tiết công nghệ, thư viện bên thứ ba và hạ tầng:

- **HANA Database Repositories:**
  - Sử dụng `hdbcli` hoặc connection pool để thực thi câu lệnh SQL với SAP HANA Express/Cloud.
  - Cung cấp triển khai cụ thể cho: `HanaRunRepository`, `HanaTaskRepository`, `HanaWorkflowRepository`, `HanaEventLogRepository`.
- **Event Publishing Decorator Chain:**
  - Đóng gói logic xuất bản sự kiện theo chuỗi decorator 5 lớp.
- **Hệ thống Giám sát & Quản lý Bộ nhớ (Janitors):**
  - `EventLoopLagMonitor`: Đo lường độ trễ event loop của Python asyncio.
  - `MallocTrimJanitor`: Định kỳ thu hồi bộ nhớ dư thừa từ arena glibc.
  - `CacheExpiryJanitor`: Quét và dọn các bản ghi cache hết hạn TTL.
