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
| **AI Eagle Platform** | Hybrid Deduplication (Exact + Fuzzy + Vector + Graph), SAP HANA REAL_VECTOR(640), Material SDS Analysis, Governance Smart API | 📖 [Xem Tài Liệu](./docs/eagle/) |
| **Data Factory Platform** | High-Throughput Migration Engine, Polars SIMD Vectorized, Adaptive Batching (cgroups v1/v2), 126+ Rule Difficulty Router, SAP Delivery Tables | 📖 [Xem Tài Liệu](./docs/data-factory/) |
| **Worker SDK (Package)** | Thư viện phát triển Workflow Workers (Server, Pull, Headless modes, gRPC/REST, Streaming I/O) | 📦 [Xem Mã Nguồn](./packages/worker-sdk/) |
| **Agent SDK (Package)** | Thư viện phát triển Autonomous AI Agents (LangGraph, SAP HANA Checkpointing, HITL, Context Compaction) | 📦 [Xem Mã Nguồn](./packages/agent-sdk/) |
| **Eagle Platform Core (Package)** | Bộ đôi Smart Service SDK & Governance Smart API phát hiện trùng lặp dữ liệu và phân tích hóa chất SDS | 📦 [Xem Mã Nguồn](./packages/eagle/) |
| **Data Factory Core (Package)** | Nền tảng di trú, chuẩn hóa và kiểm tra dữ liệu lớn (Migration, Validation, Transformation, Schema Mapping) | 📦 [Xem Mã Nguồn](./packages/data-factory/) |
| *(Mở Rộng Trong Tương Lai)* | *Knowledge Graph Engine, SAP Integration Engine...* | *(Đang cập nhật)* |

---

## 📦 Mã Nguồn Các Bộ SDK & Core Packages

Toàn bộ mã nguồn phát triển chính thức của các SDK và gói dịch vụ lõi hiện đã được đưa vào thư mục [`packages/`](./packages/):

- 🛠️ **[`packages/worker-sdk/`](./packages/worker-sdk/):** Bộ công cụ phát triển Worker cho hệ thống quy trình AI Workflow. Hỗ trợ 3 chế độ chạy (`SERVER`, `PULL`, `HEADLESS`), đa giao thức `gRPC` (:50051) & `REST HTTP`, giải quyết tham chiếu tệp qua File Service, và tối ưu ngân sách kết quả `ResultBudget`.
- 🤖 **[`packages/agent-sdk/`](./packages/agent-sdk/):** Bộ công cụ phát triển tác nhân AI tự trị trên nền tảng **LangGraph**. Cung cấp 4 bộ dựng đồ thị (`ToolAgentBuilder`, `GraphAgentBuilder`, `FlowAgentBuilder`, `SubGraphAgentBuilder`), lưu vết trạng thái phân tán trên **SAP HANA** (`HanaCheckpointSaver`), tương tác phê duyệt Human-In-The-Loop (`HITL`), và nén ngữ cảnh thông minh qua `ContextBudgetManager`.
- 🦅 **[`packages/eagle/`](./packages/eagle/):** Trọn bộ mã nguồn lõi của nền tảng **AI Eagle Platform** bao gồm:
  - `smart-service-sdk`: Động cơ so khớp trùng lặp lai (Exact, Fuzzy, `REAL_VECTOR(640)`, Graph Evidence), phân tích tài liệu hóa chất SDS, và tìm kiếm tương đồng.
  - `governance-smart-api`: Cổng API hướng caller cho các tác vụ nạp chỉ mục trùng lặp ngầm (Background Jobs), nạp tệp CSV và giao diện UI Console trực quan.
- 🏭 **[`packages/data-factory/`](./packages/data-factory/):** Nền tảng Data Factory Engine hiệu năng cao cho dữ liệu lớn:
  - `layer1_domain`: Mô hình dữ liệu di trú (`JobStatus`, `MigrationPlan`, `ValidationRule`, `FieldMapping`).
  - `layer2_application`: Các động cơ nghiệp vụ lõi (`data_migration`, `data_validation`, `data_transformation`, `schema_transform`, `bundle`, `reference_data`, `rule_management`).
  - `layer3_adapters`: Bộ điều khiển REST Controller và SSE Event Streams cho tiến độ thời gian thực.
  - `layer4_frameworks`: Bộ thích ứng cgroups v1/v2 tự động chia mẻ bộ nhớ (`AdaptiveBatchSizeManager`), Polars SIMD Vectorized engine, SAP HANA Reader & Delivery table writer.

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

## 🦅 Tiêu Điểm 6: AI Eagle Platform (Smart Deduplication & Material SDS Analysis)

Nền tảng phát hiện dữ liệu trùng lặp thông minh (Deduplication) và phân tích tài liệu kỹ thuật hóa chất SDS kết hợp SAP HANA Vector Engine và Graph Workspace:

### Cấu Trúc Module:
- 🏛️ **[01. Kiến Trúc Tổng Thể](./docs/eagle/01-architecture/):**
  - [Tổng Quan & Các Khái Niệm Cốt Lõi: Bài toán trùng lặp Master Data & Đường ống so khớp lai 4 tầng](./docs/eagle/01-architecture/01-overview-and-concepts.md)
  - [Topology Hệ Thống & Cổng Giao Tiếp: governance-smart-api (:8080) & smart-service-sdk (:8088)](./docs/eagle/01-architecture/02-system-topology.md)
  - [Kiến Trúc Phân Tầng Clean Architecture 4 Lớp & Cơ Chế Nhúng Hợp Nhất Tiến Trình](./docs/eagle/01-architecture/03-clean-architecture-and-composition.md)
- 🔍 **[02. Động Cơ So Khớp Trùng Lặp Lai (Duplicate Detection Engine)](./docs/eagle/02-duplicate-detection-engine/):**
  - [Đường Ống So Khớp Lai: Exact Match, Fuzzy Match (Jaro-Winkler/Levenshtein) & Tổng Hợp Điểm](./docs/eagle/02-duplicate-detection-engine/01-hybrid-matching-pipeline.md)
  - [Tương Đồng Vector & Nhúng Ngôn Ngữ: SAP HANA REAL_VECTOR(640) & FastEmbed bge-small-en-v1.5](./docs/eagle/02-duplicate-detection-engine/02-vector-similarity-and-embeddings.md)
  - [Bằng Chứng Đồ Thị & Vết Quyết Định Minh Bạch: Decision Trace JSON & Audit Trail](./docs/eagle/02-duplicate-detection-engine/03-graph-evidence-and-decision-trace.md)
  - [Mở Rộng Từ Khóa Bằng LLM: LLM Term Expansion & Cơ Chế Ngắt Mạch Circuit Breaker](./docs/eagle/02-duplicate-detection-engine/04-llm-term-expansion.md)
- 🕸️ **[03. Đồ Thị Tri Thức & Lưu Trữ SAP HANA (Knowledge Graph & RAG)](./docs/eagle/03-knowledge-graph-and-rag/):**
  - [Mô Hình Dữ Liệu SAP HANA: Schema các bảng AE_RAG_DOCUMENTS, CHUNKS, ENTITIES, RELATIONS](./docs/eagle/03-knowledge-graph-and-rag/01-sap-hana-ae-data-model.md)
  - [Đồ Thị Tri Thức AE_GRAPH_WORKSPACE & Trích Xuất Thực Thể Tự Động Bằng spaCy](./docs/eagle/03-knowledge-graph-and-rag/02-graph-workspace-and-mentions.md)
- 🧪 **[04. Phân Tích Dữ Liệu An Toàn Hóa Chất (Material SDS Analysis)](./docs/eagle/04-material-sds-analysis/):**
  - [Động Cơ Phân Tích Bảng Dữ Liệu Hóa Chất (Safety Data Sheet - SDS) Chuẩn Quốc Tế GHS](./docs/eagle/04-material-sds-analysis/01-material-sds-analysis-engine.md)
  - [Làm Sạch, Làm Giàu Dữ Liệu & Động Cơ AI Gợi Ý Luật Quản Trị Dữ Liệu Tối Ưu](./docs/eagle/04-material-sds-analysis/02-cleansing-enrichment-and-rule-suggestions.md)
- 💼 **[05. Cổng Quản Trị & Giao Diện Điều Khiển (Governance Smart API)](./docs/eagle/05-governance-smart-api/):**
  - [Nhập Dữ Liệu Chỉ Mục Ngầm: Background Jobs Khối Lượng Lớn & Polling Trạng Thái](./docs/eagle/05-governance-smart-api/01-request-driven-duplicate-import.md)
  - [Nạp Tệp Trực Tiếp & Giao Diện Quản Trị Trực Quan In-App UI Console (/ui)](./docs/eagle/05-governance-smart-api/02-csv-upload-and-inline-console.md)

---

## 🏭 Tiêu Điểm 7: Data Factory Platform (High-Throughput Migration & Rule Engine)

Nền tảng di trú, chuẩn hóa và kiểm tra chất lượng dữ liệu lớn chuyên sâu cho SAP S/4HANA theo mô hình **"HTTP as Trigger, Database as Delivery"** với khả năng xử lý hàng triệu bản ghi:

### Cấu Trúc Module:
- 🏛️ **[01. Kiến Trúc Tổng Thể](./docs/data-factory/01-architecture/):**
  - [Tổng Quan & Các Khái Niệm Cốt Lõi: Mô hình Trigger-Delivery, Hai Bảng Đích DF_REPORT & DF_CB](./docs/data-factory/01-architecture/01-overview-and-concepts.md)
  - [Topology Mạng & Cổng Giao Tiếp: Cổng HTTP :8000, Server-Sent Events (SSE) & XSUAA](./docs/data-factory/01-architecture/02-system-topology.md)
  - [Kiến Trúc Phân Tầng Clean Architecture 4 Lớp & Quy Tắc Phụ Thuộc](./docs/data-factory/01-architecture/03-clean-architecture-and-layers.md)
- 🚀 **[02. Động Cơ Di Trú Dữ Liệu (Data Migration Engine)](./docs/data-factory/02-data-migration-engine/):**
  - [Khởi Tạo Tác Vụ & Tính Bất Khả Trùng Lặp (Idempotency) Với HTTP 202 Accepted](./docs/data-factory/02-data-migration-engine/01-job-dispatch-and-idempotency.md)
  - [Cơ Chế Bàn Giao Dữ Liệu Bằng Hai Bảng Vật Lý SAP HANA: DF_REPORT_<job_id> & DF_CB_<job_id>](./docs/data-factory/02-data-migration-engine/02-delivery-tables-and-reporting.md)
  - [Theo Dõi Tiến Độ Thời Gian Thực Bằng Server-Sent Events (SSE) & Giao Thức Progress JSON](./docs/data-factory/02-data-migration-engine/03-sse-events-and-progress-tracking.md)
- 🎯 **[03. Bộ Máy Kiểm Tra & Danh Mục Quy Tắc (Validation & Rule Engine)](./docs/data-factory/03-validation-and-rule-engine/):**
  - [Danh Mục 126+ Quy Tắc Kiểm Tra Chất Lượng Dữ Liệu Sản Xuất Chuẩn Doanh Nghiệp](./docs/data-factory/03-validation-and-rule-engine/01-validation-rules-catalog.md)
  - [Bộ Định Tuyến Độ Khó Quy Tắc 3 Cấp Độ (Rule Difficulty Router): Simple, Medium, Hard](./docs/data-factory/03-validation-and-rule-engine/02-rule-difficulty-router.md)
- 🔄 **[04. Động Cơ Chuyển Đổi Dữ Liệu (Transformation Engine)](./docs/data-factory/04-transformation-engine/):**
  - [Biến Đổi Cột & Biến Đổi Dòng: SIMD Vectorized Polars vs Row Transformer Callback Engine](./docs/data-factory/04-transformation-engine/01-row-and-column-transformations.md)
  - [Ánh Xạ Cấu Trúc Dữ Liệu Đích (Schema Transformation): Đổi tên, Ép kiểu, Bổ sung trường SAP](./docs/data-factory/04-transformation-engine/02-schema-transformation.md)
- ⚡ **[05. Hiệu Năng & Khả Năng Chịu Tải (Performance & Resilience)](./docs/data-factory/05-performance-and-resilience/):**
  - [Kích Thước Mẻ Thích Ứng (Adaptive Batching) Tự Động Thăm Dò cgroups v1/v2 Chống OOM-Killed](./docs/data-factory/05-performance-and-resilience/01-adaptive-batching-and-cgroups.md)
  - [Cơ Chế Giải Mã Chứng Thư Dùng Một Lần (Single-Use Resolve-Token) Bảo Mật Tuyệt Đối](./docs/data-factory/05-performance-and-resilience/02-credential-resolution-and-security.md)

---

## 🛠️ Nguyên Tắc Thiết Kế Cốt Lõi (Core Principles)

1. **Clean Architecture:** Tách biệt tuyệt đối giữa tầng nghiệp vụ Domain/Application và tầng hạ tầng Frameworks/Drivers.
2. **HANA as System of Record:** Lưu trữ dữ liệu gốc, vectors, Data Vault, Star Schema, File Metadata, chỉ mục Eagle và bảng di trú Data Factory tập trung trong SAP HANA để đảm bảo toàn vẹn ACID và RBAC.
3. **Auditability & Traceability:** Data Vault 2.0, File Versioning, Decision Trace của AI Eagle và bảng báo cáo lỗi `DF_REPORT_<job_id>` của Data Factory đảm bảo dữ liệu kiểm toán 100%.
4. **Interactive & AI-Augmented Analytics:** Biểu đồ tương tác lọc chéo mượt mà kết hợp trợ lý AI Copilot chuyển đổi ngôn ngữ tự nhiên thành biểu đồ trong 5 giây.
5. **High-Performance Tiered Storage:** Đệm đĩa cứng SSD cục bộ giúp phản hồi API trong vài mili-giây, kết hợp lưu trữ lâu dài bền vững trên Cloud S3 / SeaweedFS.
6. **Multi-Model Intelligence & Vectorized Engine:** Kết hợp đồng thời Exact rules, Fuzzy text algorithms, Dense Vectors (`REAL_VECTOR(640)`), Graph Workspaces và Polars SIMD Vectorized Column Engine để giải quyết bài toán chất lượng dữ liệu với độ chính xác và tốc độ tối đa.
