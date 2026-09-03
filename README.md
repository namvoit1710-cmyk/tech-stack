# Tech-Stack Knowledge Hub

> **Kho lưu trữ tài liệu kiến trúc, thiết kế kỹ thuật và các nền tảng công nghệ (Architecture & Tech-Stack Documentation).**

Chào mừng bạn đến với **Tech-Stack Knowledge Hub**. Đây là nơi tổng hợp các tài liệu phân tích kỹ thuật, sơ đồ kiến trúc, giải pháp thiết kế hệ thống và tiêu chuẩn công nghệ cho các phân hệ phần mềm hiện đại của SimpleMDG.

---

## 📂 Danh Mục Các Phân Hệ Công Nghệ (Tech Stacks)

| Phân Hệ / Dự Án | Lĩnh Vực & Công Nghệ Cốt Lõi | Tài Liệu Chi Tiết |
|---|---|---|
| **AI Workflow Management** | Event-Driven Orchestration, Clean Architecture 4 Lớp, FastAPI, Socket.IO, gRPC, 21 Node Types, Worker SDK, Kafka, SAP HANA | 📖 [Xem Tài Liệu](./docs/ai-workflow-management/) |
| **AI Agent Ecosystem** | Autonomous AI Agents, LangGraph, Multi-Agent Orchestration, Agent SDK, HITL Checkpointing, SAP BTP, LaidonLLM Gateway | 📖 [Xem Tài Liệu](./docs/agent/) |
| **HANA RAG Service** | SAP HANA-First RAG, `REAL_VECTOR`, Structured SQL Pushdown, GraphRAG-lite, Hybrid RRF Rerank, Presidio PII Masking, Smart Tools | 📖 [Xem Tài Liệu](./docs/hana-rag-service/) |
| **BI Dashboard & Analytics**| Data Vault 2.0 (Hubs/Links/Sats), OLAP Star Schema, SAP HANA Columnar Engine, React 19 Canvas (@dnd-kit), AI Chat-to-Chart | 📖 [Xem Tài Liệu](./docs/bi-dashboard/) |
| **File Service** | Tiered Object Storage (SSD Hot Tier + S3/SeaweedFS Cold Tier), Zero-Downtime Versioning, CSV `row_id` Canonicalization, Presigned Multipart | 📖 [Xem Tài Liệu](./docs/file-service/) |
| **Worker SDK (Package)** | Thư viện phát triển Workflow Workers (Server, Pull, Headless modes, gRPC/REST, Streaming I/O) | 📦 [Xem Mã Nguồn](./packages/worker-sdk/) |
| **Agent SDK (Package)** | Thư viện phát triển Autonomous AI Agents (LangGraph, SAP HANA Checkpointing, HITL, Context Compaction) | 📦 [Xem Mã Nguồn](./packages/agent-sdk/) |
| *(Mở Rộng Trong Tương Lai)* | *Data Migration Platform, Knowledge Graph Engine, SAP Integration Engine...* | *(Đang cập nhật)* |

---

## 📦 Mã Nguồn Các Bộ SDK (Developer Packages)

Toàn bộ mã nguồn phát triển chính thức của các SDK hiện đã được đưa vào thư mục [`packages/`](./packages/):

- 🛠️ **[`packages/worker-sdk/`](./packages/worker-sdk/):** Bộ công cụ phát triển Worker cho hệ thống quy trình AI Workflow. Hỗ trợ 3 chế độ chạy (`SERVER`, `PULL`, `HEADLESS`), đa giao thức `gRPC` (:50051) & `REST HTTP`, giải quyết tham chiếu tệp qua File Service, và tối ưu ngân sách kết quả `ResultBudget`.
- 🤖 **[`packages/agent-sdk/`](./packages/agent-sdk/):** Bộ công cụ phát triển tác nhân AI tự trị trên nền tảng **LangGraph**. Cung cấp 4 bộ dựng đồ thị (`ToolAgentBuilder`, `GraphAgentBuilder`, `FlowAgentBuilder`, `SubGraphAgentBuilder`), lưu vết trạng thái phân tán trên **SAP HANA** (`HanaCheckpointSaver`), tương tác phê duyệt Human-In-The-Loop (`HITL`), và nén ngữ cảnh thông minh qua `ContextBudgetManager`.

---

## 🔍 Tiêu Điểm 1: AI Workflow Management (Enterprise Event-Driven Engine)

Hệ thống điều phối quy trình AI cốt lõi theo triết lý **Event-Driven Orchestration** (lấy cảm hứng từ [n8n](https://n8n.io/) và được nâng cấp chuẩn doanh nghiệp):

### Cấu Trúc Module:
- 🏛️ **[01. Kiến Trúc Tổng Thể](./docs/ai-workflow-management/01-architecture/):** Triết lý Event-First, Topology mạng, Clean Architecture 4 tầng.
- ⚙️ **[02. Bộ Máy Điều Phối (Engine)](./docs/ai-workflow-management/02-engine/):** Uniform Execution (`_exec_uniform`), Bảng 21 Node Types, Fanout & BFS Cascade-Skip, Vòng lặp lồng nhau, Sub-workflow Coordinator.
- ⚡ **[03. Hệ Thống Sự Kiện & Realtime](./docs/ai-workflow-management/03-event-system/):** Decorator Pattern 5 tầng (WAL, Broadcast, Enrich, Task Fork, Backend), Realtime Lane Queues (CRITICAL, NORMAL, BULK).
- 🤖 **[04. Hệ Sinh Thái Workers](./docs/ai-workflow-management/04-workers/):** Worker Executor Service, Worker SDK, Danh mục 14 Workers, Tích hợp API Gateway & OpenAPI Importer.
- 🛡️ **[05. Tính Bền Vững & Quản Lý Dữ Liệu](./docs/ai-workflow-management/05-resilience/):** Biểu thức n8n `{{ ... }}`, Streaming dữ liệu lớn (CSV/BLOB), CAS Retry, Transactional Outbox, Run Generation Guard (`TASK_SUPERSEDED`).

---

## 🤖 Tiêu Điểm 2: AI Agent Ecosystem (LangGraph Multi-Agent Platform)

Hệ sinh thái các tác nhân AI tự trị có khả năng suy luận, lập kế hoạch và phối hợp đa tác nhân xây dựng trên nền tảng **LangGraph** và **FastAPI**:

### Cấu Trúc Module:
- 🏛️ **[01. Kiến Trúc Tổng Thể](./docs/agent/01-architecture/):** So sánh Workflow vs Agent, Topology đa tác nhân phân cấp, Clean Architecture trong Agent SDK.
- 🛠️ **[02. Bộ Công Cụ Phát Triển Agent SDK](./docs/agent/02-agent-sdk/):** 4 bộ dựng đồ thị (Tool, Graph, Flow, SubGraph), Checkpointing SAP HANA, Human-in-the-loop (`interrupt`/`resume`), Context Budget Manager (5 chiến lược nén), Dual Transports.
- 👑 **[03. Bộ Điều Phối Hội Thoại Đa Tác Nhân (Orchestrator)](./docs/agent/03-orchestrator/):** Kiến trúc 6 tầng (Guardrails, Intent Classifier, Planner, JourneyTracker), Lựa chọn Agent động và Hand-off delegation.
- 🎯 **[04. Các Tác Nhân Nghiệp Vụ Chuyên Biệt (Domain Agents)](./docs/agent/04-domain-agents/):** Workflow Designer Agent, Governance Schema Agent, Validation Rule Agent, Data & File Agents, Troubleshooting Agent (RCA).

---

## 📚 Tiêu Điểm 3: HANA RAG Service (Enterprise SAP HANA-First RAG Platform)

Nền tảng Retrieval-Augmented Generation doanh nghiệp đặt **SAP HANA** làm nguồn dữ liệu chân lý duy nhất (System of Record), xóa bỏ hoàn toàn hiện tượng phân mảnh dữ liệu (Data Sprawl):

### Cấu Trúc Module:
- 🏛️ **[01. Kiến Trúc Tổng Thể](./docs/hana-rag-service/01-architecture/):** Triết lý SAP HANA as System of Record, REAL_VECTOR & Dual Workloads (API vs WORKER), Topology, Clean Architecture.
- 📥 **[02. Quy Trình Nhập Liệu (Ingestion Pipeline)](./docs/hana-rag-service/02-ingestion-pipeline/):** Bóc tách đa định dạng (PDF OCR Docling), Xử lý bảng tính CSV/XLSX bằng dữ liệu quan hệ, Parent-Child Chunks, Che giấu PII với Presidio, Ingestion Jobs.
- 🔎 **[03. Bộ Máy Truy Xuất Lai (Retrieval Engine)](./docs/hana-rag-service/03-retrieval-engine/):** Định tuyến Structured vs Unstructured, SQL Pushdown in-memory, Dense Vector 2 giai đoạn, bm25s Lexical Rerank, GraphRAG-lite, Redis Semantic Cache.
- ✍️ **[04. Sinh Câu Trả Lời Có Căn Cứ (Grounded Generation)](./docs/hana-rag-service/04-grounded-generation/):** Evidence Gating chống ảo giác, Trích dẫn nguồn chi tiết, Fast Answer vs Deep Answer, Claim Verification.
- 🧰 **[05. Bộ Công Cụ Thông Minh & Nền Tảng (Smart Tools & Platform)](./docs/hana-rag-service/05-smart-tools-and-platform/):** Smart Tools Catalog (match/dedupe, keyword-screen, extract, classify), Bảo mật Fail-Closed, SSRF Protection.

---

## 📊 Tiêu Điểm 4: BI Dashboard & Analytics Platform (Data Vault 2.0 to Star Schema)

Nền tảng Business Intelligence thế hệ mới kết hợp sức mạnh lưu trữ lịch sử bất biến của **Data Vault 2.0** với tốc độ phân tích siêu tốc của **OLAP Star Schema** trên **SAP HANA In-Memory**:

### Cấu Trúc Module:
- 🏛️ **[01. Kiến Trúc Tổng Thể](./docs/bi-dashboard/01-architecture/):**
  - [Chuỗi Chuyển Đổi 3 Tầng: Staging ➔ Data Vault 2.0 ➔ Star Schema ➔ Canvas Studio](./docs/bi-dashboard/01-architecture/01-overview-and-concepts.md)
  - [Topology Hệ Thống, Kết Nối FastAPI (:8001) & React 19 (:3000), Tích Hợp XSUAA](./docs/bi-dashboard/01-architecture/02-system-topology.md)
  - [Chuỗi 11 Giai Đoạn Vận Hành Pipeline Tuyến Tính (WF1 ➔ WF6) & Variable Bus](./docs/bi-dashboard/01-architecture/03-end-to-end-pipeline-stages.md)
- 🗄️ **[02. Động Cơ Data Vault 2.0 (Data Vault Engine)](./docs/bi-dashboard/02-data-vault-engine/):**
  - [Mô Hình Hóa Data Vault 2.0: Hubs, Links, Satellites, Hash Diff & Satellite Splitting](./docs/bi-dashboard/02-data-vault-engine/01-data-vault-2.0-modeling.md)
  - [Tối Ưu Truy Vấn Lịch Sử Dưới 100ms Bằng Point-In-Time (PIT) & Bridge Tables](./docs/bi-dashboard/02-data-vault-engine/02-pit-and-bridge-tables.md)
  - [Tự Động Sinh DDL VARBINARY(32) SAP HANA, Kịch Bản Nạp ELT & Xử Lý Schema Drift](./docs/bi-dashboard/02-data-vault-engine/03-schema-generation-and-ddl.md)
- ⭐ **[03. Mô Hình Hình Sao & Truy Vấn OLAP (OLAP Star Schema)](./docs/bi-dashboard/03-olap-star-schema/):**
  - [Bản Kê Mô Hình Hình Sao (StarManifest): Facts, Dimensions & Conformed Dimensions](./docs/bi-dashboard/03-olap-star-schema/01-star-schema-manifest.md)
  - [Bộ Sinh Chiều Thời Gian Đa Cấp: Lịch Dương Chuẩn & Năm Tài Chính Doanh Nghiệp](./docs/bi-dashboard/03-olap-star-schema/02-date-dimension-generator.md)
  - [Biên Soạn Truy Vấn SQL Động (ComposedQuery), Toán Tử Tập Hợp & Nhúng RLS](./docs/bi-dashboard/03-olap-star-schema/03-dynamic-query-composition.md)
- 📈 **[04. Xưởng Biểu Đồ & Không Gian Làm Việc (Dashboard & Charts)](./docs/bi-dashboard/04-dashboard-and-charts/):**
  - [Xưởng Thiết Kế Biểu Đồ: 11 Visualization Types (Bar, Line, Pie, Funnel, KPI...)](./docs/bi-dashboard/04-dashboard-and-charts/01-chart-studio-and-types.md)
  - [Không Gian Kéo Thả Canvas (@dnd-kit), Tương Tác Lọc Chéo & Dashboard State](./docs/bi-dashboard/04-dashboard-and-charts/02-dashboard-canvas-and-cross-filtering.md)
  - [Đồ Thị Tri Thức Metadata Graph & Trợ Lý AI Tự Động Tạo Biểu Đồ (Chat-to-Chart)](./docs/bi-dashboard/04-dashboard-and-charts/03-knowledge-graph-and-ai-chat.md)
- 🛡️ **[05. Bảo Mật & Quản Trị Hệ Thống (Governance & Resilience)](./docs/bi-dashboard/05-governance-and-resilience/):**
  - [Bảo Mật Dòng Cưỡng Bức (Row-Level Security) & 4 Chính Sách Che Giấu Cột (Masking)](./docs/bi-dashboard/05-governance-and-resilience/01-row-level-security-and-masking.md)
  - [Tự Động Phát Hiện Lệch Cấu Trúc (Schema Drift), Dead-Letter Queue & Audit Trail](./docs/bi-dashboard/05-governance-and-resilience/02-schema-drift-and-monitoring.md)

---

## 📁 Tiêu Điểm 5: File Service (Tiered Storage & Versioning Platform)

Nền tảng lưu trữ đối tượng phân tầng và quản lý phiên bản tệp doanh nghiệp hỗ trợ AWS S3, SeaweedFS và bộ đệm Hot Tier SSD siêu tốc:

### Cấu Trúc Module:
- 🏛️ **[01. Kiến Trúc Tổng Thể](./docs/file-service/01-architecture/):**
  - [Tổng Quan & Các Khái Niệm Cốt Lõi: file_id vs version_id, Raw vs Processed, OCC](./docs/file-service/01-architecture/01-overview-and-core-concepts.md)
  - [Topology Hệ Thống & Cổng Giao Tiếp: Tầng Nóng Local SSD & Tầng Lạnh Cloud S3](./docs/file-service/01-architecture/02-system-topology.md)
  - [Kiến Trúc Phân Tầng Clean Architecture 4 Lớp & Các Interfaces Cốt Lõi](./docs/file-service/01-architecture/03-clean-architecture-and-layers.md)
- 🗄️ **[02. Động Cơ Lưu Trữ Đối Tượng (Storage Engine)](./docs/file-service/02-storage-engine/):**
  - [Bộ Lưu Trữ Phân Tầng (TieredStorageProvider): Ghi đệm Write-Behind & Thu hồi đĩa LRU](./docs/file-service/02-storage-engine/01-tiered-storage-provider.md)
  - [Các Nhà Cung Cấp Lưu Trữ: AWS S3, MinIO, Cloudflare R2 & SeaweedFS Filer](./docs/file-service/02-storage-engine/02-s3-and-seaweedfs-providers.md)
  - [Kho Siêu Dữ Liệu Tệp SAP HANA: Schema FILES, FILE_VERSIONS, SESSIONS & Phòng Ngừa Injection](./docs/file-service/02-storage-engine/03-metadata-repositories.md)
- 🔄 **[03. Vòng Đời Tệp & Giao Diện API (File Lifecycle & APIs)](./docs/file-service/03-file-lifecycle-and-apis/):**
  - [Tải Lên Trực Tiếp & Quản Lý Phiên Bản: Xử lý xung đột HTTP 409 Conflict với previous_version_id](./docs/file-service/03-file-lifecycle-and-apis/01-direct-and-versioned-uploads.md)
  - [Tải Lên Nhiều Phần Kèm Presigned URLs (Multipart Upload) Cho Tệp Lớn Hàng GBs](./docs/file-service/03-file-lifecycle-and-apis/02-presigned-multipart-upload.md)
  - [Tải Xuống & Truyền Phát Dữ Liệu: Asynchronous Chunked Streaming & Presigned GET](./docs/file-service/03-file-lifecycle-and-apis/03-download-and-streaming.md)
  - [Chuẩn Hóa & Bổ Sung Cột Định Danh CSV (Row-ID Canonicalization) Cho Toàn Hệ Sinh Thái](./docs/file-service/03-file-lifecycle-and-apis/04-csv-row-id-canonicalization.md)
- ⏱️ **[04. Hiệu Năng & Khả Năng Vận Hành (Performance & Observability)](./docs/file-service/04-performance-and-observability/):**
  - [Động Cơ Giám Sát Hiệu Năng: Đo lường Execution Time, RAM Peak, CPU & Nhật Ký JSONL Hàng Ngày](./docs/file-service/04-performance-and-observability/01-performance-monitoring-engine.md)
  - [Tính Bền Bỉ & Khả Năng Chống Chịu Lỗi: Exponential Backoff Retry & Graceful Drain](./docs/file-service/04-performance-and-observability/02-resilience-and-disk-pressure.md)

---

## 🛠️ Nguyên Tắc Thiết Kế Cốt Lõi (Core Principles)

1. **Clean Architecture:** Tách biệt tuyệt đối giữa tầng nghiệp vụ Domain/Application và tầng hạ tầng Frameworks/Drivers.
2. **HANA as System of Record:** Lưu trữ dữ liệu gốc, vectors, Data Vault, Star Schema và File Metadata tập trung trong SAP HANA để đảm bảo toàn vẹn ACID và RBAC.
3. **Auditability & Traceability:** Data Vault 2.0 và File Versioning đảm bảo dữ liệu lịch sử bất biến và kiểm toán 100%.
4. **Interactive & AI-Augmented Analytics:** Biểu đồ tương tác lọc chéo mượt mà kết hợp trợ lý AI Copilot chuyển đổi ngôn ngữ tự nhiên thành biểu đồ trong 5 giây.
5. **High-Performance Tiered Storage:** Đệm đĩa cứng SSD cục bộ giúp phản hồi API trong vài mili-giây, kết hợp lưu trữ lâu dài bền vững trên Cloud S3 / SeaweedFS.
