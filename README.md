# Tech-Stack Knowledge Hub

> **Kho lưu trữ tài liệu kiến trúc, thiết kế kỹ thuật và các nền tảng công nghệ (Architecture & Tech-Stack Documentation).**

Chào mừng bạn đến với **Tech-Stack Knowledge Hub**. Đây là nơi tổng hợp các tài liệu phân tích kỹ thuật, sơ đồ kiến trúc, giải pháp thiết kế hệ thống và tiêu chuẩn công nghệ cho các phân hệ phần mềm hiện đại.

---

## 📂 Danh Mục Các Phân Hệ Công Nghệ (Tech Stacks)

| Phân Hệ / Dự Án | Lĩnh Vực & Công Nghệ Cốt Lõi | Tài Liệu Chi Tiết |
|---|---|---|
| **AI Workflow Management** | Event-Driven Orchestration, Clean Architecture, FastAPI, Socket.IO, gRPC, Celery/Worker SDK, Kafka, SAP HANA | 📖 [Xem Tài Liệu](./docs/ai-workflow-management/) |
| *(Mở Rộng Trong Tương Lai)* | *RAG System, Data Migration, Knowledge Graph, SAP Integration...* | *(Đang cập nhật)* |

---

## 🔍 Tiêu Điểm: AI Workflow Management

Hệ thống điều phối quy trình AI cốt lõi theo triết lý **Event-Driven Orchestration** (lấy cảm hứng từ [n8n](https://n8n.io/) và được nâng cấp chuẩn doanh nghiệp):

### Cấu Trúc Module Tài Liệu:
- 🏛️ **[01. Kiến Trúc Tổng Thể](./docs/ai-workflow-management/01-architecture/):**
  - [Tổng Quan & Triết Lý Thiết Kế](./docs/ai-workflow-management/01-architecture/01-overview-and-philosophy.md)
  - [Topology Hệ Thống & Cổng Giao Tiếp](./docs/ai-workflow-management/01-architecture/02-system-topology.md)
  - [Kiến Trúc Phân Tầng Clean Architecture 4 Lớp](./docs/ai-workflow-management/01-architecture/03-clean-architecture-layers.md)
- ⚙️ **[02. Bộ Máy Điều Phối (Engine)](./docs/ai-workflow-management/02-engine/):**
  - [Mô Hình Thực Thi Thống Nhất (Uniform Execution)](./docs/ai-workflow-management/02-engine/01-uniform-execution-model.md)
  - [Thuật Toán Fanout & BFS Cascade-Skip](./docs/ai-workflow-management/02-engine/02-fanout-and-cascade-skip.md)
  - [Vòng Lặp Lồng Nhau & Phân Cấp Phạm Vi (Scope Hierarchy)](./docs/ai-workflow-management/02-engine/03-loop-and-scope-hierarchy.md)
- ⚡ **[03. Hệ Thống Sự Kiện & Realtime](./docs/ai-workflow-management/03-event-system/):**
  - [Chuỗi Xuất Bản Sự Kiện Decorator Pattern](./docs/ai-workflow-management/03-event-system/01-event-publishing-chain.md)
  - [Phân Phối Realtime qua Socket.IO & Push Gateway](./docs/ai-workflow-management/03-event-system/02-realtime-delivery.md)
- 🤖 **[04. Hệ Sinh Thái Workers](./docs/ai-workflow-management/04-workers/):**
  - [Worker Executor Service & Điều Phối Task](./docs/ai-workflow-management/04-workers/01-worker-executor-service.md)
  - [Worker SDK & Hướng Dẫn Xây Dựng Worker](./docs/ai-workflow-management/04-workers/02-worker-sdk.md)
  - [Danh Mục Các Worker Tiêu Biểu (HTTP, Agent LLM, Data Mapping...)](./docs/ai-workflow-management/04-workers/03-workers-catalog.md)
- 🛡️ **[05. Tính Bền Vững & Quản Lý Dữ Liệu](./docs/ai-workflow-management/05-resilience/):**
  - [Luồng Dữ Liệu & Phân Giải Biến Biểu Thức](./docs/ai-workflow-management/05-resilience/01-data-flow-and-variables.md)
  - [Tối Ưu Hóa Enterprise (CAS Retry, Outbox, Memory Janitors)](./docs/ai-workflow-management/05-resilience/02-production-hardening.md)

---

## 🛠️ Nguyên Tắc Thiết Kế Chung (Design Principles)

1. **Clean Architecture:** Tách biệt tuyệt đối giữa tầng nghiệp vụ Domain/Application và tầng hạ tầng Frameworks/Drivers.
2. **Event Sourcing & Replayability:** Nhật ký sự kiện (Event Log) là chân lý, mọi trạng thái hệ thống đều có thể tái hiện chính xác.
3. **Mở Rộng Không Giới Hạn (Horizontal Scalability):** Các dịch vụ điều phối và thực thi đều là stateless, mở rộng dễ dàng theo nhu cầu tải.
4. **Giám Sát & Tự Phục Hồi:** Tích hợp sẵn cơ chế giám sát nhịp tim, thu hồi bộ nhớ tự động (`malloc_trim`), và xử lý nghẽn tải (backpressure).
