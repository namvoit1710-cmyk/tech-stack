# Tech-Stack Knowledge Hub

> **Kho lưu trữ tài liệu kiến trúc, thiết kế kỹ thuật và các nền tảng công nghệ (Architecture & Tech-Stack Documentation).**

Chào mừng bạn đến với **Tech-Stack Knowledge Hub**. Đây là nơi tổng hợp các tài liệu phân tích kỹ thuật, sơ đồ kiến trúc, giải pháp thiết kế hệ thống và tiêu chuẩn công nghệ cho các phân hệ phần mềm hiện đại.

---

## 📂 Danh Mục Các Phân Hệ Công Nghệ (Tech Stacks)

| Phân Hệ / Dự Án | Lĩnh Vực & Công Nghệ Cốt Lõi | Tài Liệu Chi Tiết |
|---|---|---|
| **AI Workflow Management** | Event-Driven Orchestration, Clean Architecture 4 Lớp, FastAPI, Socket.IO, gRPC, 21 Node Types, Worker SDK, Kafka, SAP HANA | 📖 [Xem Toàn Bộ Tài Liệu](./docs/ai-workflow-management/) |
| *(Mở Rộng Trong Tương Lai)* | *RAG System, Data Migration, Knowledge Graph, SAP Integration...* | *(Đang cập nhật)* |

---

## 🔍 Tiêu Điểm: AI Workflow Management (Enterprise Event-Driven Engine)

Hệ thống điều phối quy trình AI cốt lõi theo triết lý **Event-Driven Orchestration** (lấy cảm hứng từ [n8n](https://n8n.io/) và được nâng cấp chuẩn doanh nghiệp):

### Cấu Trúc Module Tài Liệu Đầy Đủ:
- 🏛️ **[01. Kiến Trúc Tổng Thể](./docs/ai-workflow-management/01-architecture/):**
  - [01. Tổng Quan & Triết Lý Thiết Kế](./docs/ai-workflow-management/01-architecture/01-overview-and-philosophy.md): Động lực, Event-First, Runtime Validation Only, Engine Split.
  - [02. Topology Hệ Thống & Cổng Giao Tiếp](./docs/ai-workflow-management/01-architecture/02-system-topology.md): Bản đồ dịch vụ, bảng cổng (8000, 8001, 50051, 8004, 35000+), HANA, Kafka, Redis.
  - [03. Kiến Trúc Phân Tầng Clean Architecture 4 Lớp](./docs/ai-workflow-management/01-architecture/03-clean-architecture-layers.md): Domain Core, Application Use Cases, Interface Adapters, Frameworks/Drivers.
- ⚙️ **[02. Bộ Máy Điều Phối (Engine)](./docs/ai-workflow-management/02-engine/):**
  - [01. Mô Hình Thực Thi Thống Nhất & 21 Node Types](./docs/ai-workflow-management/02-engine/01-uniform-execution-model.md): Phương thức `_exec_uniform`, 6 completion modes, bảng chi tiết 21 node types thực tế.
  - [02. Thuật Toán Fanout & BFS Cascade-Skip](./docs/ai-workflow-management/02-engine/02-fanout-and-cascade-skip.md): Hàm thuần túy `compute_fanout()`, BFS loại bỏ nhánh phụ thuộc, Predecessor Readiness.
  - [03. Vòng Lặp & Phân Cấp Phạm Vi Biến](./docs/ai-workflow-management/02-engine/03-loop-and-scope-hierarchy.md): Vòng lặp lồng nhau (Nested Loops), định danh xác định `invocation_id` (uuid5), ngăn xếp `scope_stack`, và chuỗi Chain of Responsibility của `ResolutionScope`.
  - [04. Điều Phối Sub-Workflow & Quy Trình Con](./docs/ai-workflow-management/02-engine/04-child-workflows-and-subflows.md): Bộ điều phối `ChildWorkflowCoordinator`, input/output binding, phòng chống chu trình chéo `CrossWorkflowCycleError`.
- ⚡ **[03. Hệ Thống Sự Kiện & Realtime](./docs/ai-workflow-management/03-event-system/):**
  - [01. Chuỗi Xuất Bản Sự Kiện Decorator Pattern](./docs/ai-workflow-management/03-event-system/01-event-publishing-chain.md): Decorator Pattern 5 tầng (WAL, Broadcast, Enrich, Task Dispatch Fork, Backend), phân cấp ưu tiên `EventCollector`.
  - [02. Phân Phối Realtime qua Socket.IO & Push Gateway](./docs/ai-workflow-management/03-event-system/02-realtime-delivery.md): Topic `realtime.events`, cơ chế hàng đợi 3 làn (CRITICAL, NORMAL, BULK), tích hợp Push Gateway.
- 🤖 **[04. Hệ Sinh Thái Workers](./docs/ai-workflow-management/04-workers/):**
  - [01. Worker Executor Service & Điều Phối Task](./docs/ai-workflow-management/04-workers/01-worker-executor-service.md): Dynamic Registry, Heartbeat, Liveness sweeper, mô hình Push vs Pull (Pull-lease model).
  - [02. Worker SDK & Hướng Dẫn Xây Dựng Worker](./docs/ai-workflow-management/04-workers/02-worker-sdk.md): Clean Architecture trong Worker SDK, 2 chế độ SERVER vs HEADLESS.
  - [03. Danh Mục Toàn Bộ 14 Worker Trong Hệ Sinh Thái](./docs/ai-workflow-management/04-workers/03-workers-catalog.md): Chi tiết 14 worker: HTTP, Agent LLM (OpenAI, Claude), Data Mapping, Database, Wait, Code sandbox...
  - [04. Tích Hợp API Gateway & OpenAPI Importer](./docs/ai-workflow-management/04-workers/04-api-gateway-and-openapi-integration.md): Bộ nhập khẩu OpenAPI Swagger Spec tự động (`openapi_import.py`), tạo Gateway Functions đưa vào Canvas.
- 🛡️ **[05. Tính Bền Vững & Quản Lý Dữ Liệu](./docs/ai-workflow-management/05-resilience/):**
  - [01. Luồng Dữ Liệu & Phân Giải Biến Biểu Thức](./docs/ai-workflow-management/05-resilience/01-data-flow-and-variables.md): Cú pháp n8n `{{ ... }}`, 4 tầng tra cứu `VariableResolver`, streaming dữ liệu lớn (CSV/BLOB).
  - [02. Tính Bền Vững & Tối Ưu Enterprise](./docs/ai-workflow-management/05-resilience/02-production-hardening.md): Khóa lạc quan CAS Retry, Transactional Outbox, giải phóng RAM (`malloc_trim`), giám sát độ trễ event loop, Durable Timers.
  - [03. Run Generation Guard & Tái Tạo Trạng Thái](./docs/ai-workflow-management/05-resilience/03-run-generation-guard-and-replays.md): Chống Zombie Callback (`TASK_SUPERSEDED` HTTP 409), cấu trúc `ProjectedRunState` v3, Event Sourcing Replay.

---

## 🛠️ Nguyên Tắc Thiết Kế Cốt Lõi (Core Principles)

1. **Clean Architecture:** Tách biệt tuyệt đối giữa tầng nghiệp vụ Domain/Application và tầng hạ tầng Frameworks/Drivers.
2. **Event Sourcing & Replayability:** Nhật ký sự kiện (Event Log) là chân lý, mọi trạng thái hệ thống đều có thể tái hiện chính xác.
3. **Mở Rộng Không Giới Hạn (Horizontal Scalability):** Các dịch vụ điều phối và thực thi đều là stateless, mở rộng dễ dàng theo nhu cầu tải.
4. **Giám Sát & Tự Phục Hồi:** Tích hợp sẵn cơ chế giám sát nhịp tim, thu hồi bộ nhớ tự động (`malloc_trim`), và xử lý nghẽn tải (backpressure).
