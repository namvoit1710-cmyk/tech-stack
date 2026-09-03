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
| *(Mở Rộng Trong Tương Lai)* | *Data Migration Platform, Knowledge Graph Engine, SAP Integration Engine...* | *(Đang cập nhật)* |

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
- 🏛️ **[01. Kiến Trúc Tổng Thể](./docs/hana-rag-service/01-architecture/):**
  - [Triết Lý SAP HANA as System of Record, REAL_VECTOR & Dual Workloads (API vs WORKER)](./docs/hana-rag-service/01-architecture/01-overview-and-hana-core.md)
  - [Topology Hệ Thống, Cổng Mạng & Lựa Chọn Embedding Providers](./docs/hana-rag-service/01-architecture/02-system-topology.md)
  - [Clean Architecture 4 Tầng & Composition Containers Trong bootstrap.py](./docs/hana-rag-service/01-architecture/03-clean-architecture-and-containers.md)
- 📥 **[02. Quy Trình Nhập Liệu (Ingestion Pipeline)](./docs/hana-rag-service/02-ingestion-pipeline/):**
  - [Bóc Tách Đa Định Dạng: PDF (Docling OCR fallback), DOCX, TXT, MD, JSON](./docs/hana-rag-service/02-ingestion-pipeline/01-multi-format-parsing.md)
  - [Xử Lý Bảng Tính CSV & XLSX Bằng Dữ Liệu Quan Hệ Chuẩn (Không ép thành chunks vô nghĩa)](./docs/hana-rag-service/02-ingestion-pipeline/02-spreadsheets-and-structured-tables.md)
  - [Parent-Child Chunk Linking, Che Giấu PII Với Presidio & Streaming Tệp Dung Lượng Lớn](./docs/hana-rag-service/02-ingestion-pipeline/03-chunking-pii-masking-and-streaming.md)
  - [Vòng Đời Job Nạp Liệu Bất Đồng Bộ (RAG_INGESTION_JOBS) & SSE Status Streaming](./docs/hana-rag-service/02-ingestion-pipeline/04-worker-and-job-lifecycle.md)
- 🔎 **[03. Bộ Máy Truy Xuất Lai (Retrieval Engine)](./docs/hana-rag-service/03-retrieval-engine/):**
  - [Định Tuyến Truy Vấn Thông Minh & Structured SQL Pushdown Trong Bộ Nhớ HANA](./docs/hana-rag-service/03-retrieval-engine/01-query-routing-and-sql-pushdown.md)
  - [Tìm Kiếm Vector Hai Giai Đoạn, Tái Xếp Hạng bm25s, Thuật Toán RRF & HyDE](./docs/hana-rag-service/03-retrieval-engine/02-dense-vector-and-hybrid-rerank.md)
  - [GraphRAG-lite: Bóc Tách Thực Thể Bằng spaCy NLP (0$ chi phí LLM) & HANA Graph Workspace](./docs/hana-rag-service/03-retrieval-engine/03-graphrag-lite.md)
  - [Bộ Nhớ Đệm Ngữ Nghĩa (Redis Semantic Cache) & Tuyệt Đối Chống Rò Rỉ Tenant (No Tenant Bleed)](./docs/hana-rag-service/03-retrieval-engine/04-semantic-caching.md)
- ✍️ **[04. Sinh Câu Trả Lời Có Căn Cứ (Grounded Generation)](./docs/hana-rag-service/04-grounded-generation/):**
  - [Evidence Gating: Ngưỡng điểm tối thiểu chống ảo giác & Trích dẫn nguồn minh bạch](./docs/hana-rag-service/04-grounded-generation/01-evidence-gating-and-citations.md)
  - [Fast Answer Mode (< 1.5s) vs Deep Answer Mode (Tổng hợp suy luận đa nguồn)](./docs/hana-rag-service/04-grounded-generation/02-fast-vs-deep-answer-modes.md)
  - [Claim Support Verification: Thẩm định từng câu khẳng định & SSE Token Streaming](./docs/hana-rag-service/04-grounded-generation/03-claim-verification-and-sse-streaming.md)
- 🧰 **[05. Bộ Công Cụ Thông Minh & Nền Tảng (Smart Tools & Platform)](./docs/hana-rag-service/05-smart-tools-and-platform/):**
  - [Smart Tools Catalog: match/dedupe (phát hiện bản ghi trùng lặp), keyword-screen, extract, classify](./docs/hana-rag-service/05-smart-tools-and-platform/01-smart-tools-catalog.md)
  - [Bảo Mật Doanh Nghiệp: Fail-Closed Tenant Isolation, SSRF Protection, Circuit Breaker, Observability](./docs/hana-rag-service/05-smart-tools-and-platform/02-security-and-resilience.md)

---

## 🛠️ Nguyên Tắc Thiết Kế Cốt Lõi (Core Principles)

1. **Clean Architecture:** Tách biệt tuyệt đối giữa tầng nghiệp vụ Domain/Application và tầng hạ tầng Frameworks/Drivers.
2. **HANA as System of Record:** Lưu trữ dữ liệu gốc, vectors, quan hệ và đồ thị tri thức tập trung trong SAP HANA để đảm bảo toàn vẹn và bảo mật RBAC.
3. **Deterministic vs Autonomous Synergy:** Kết hợp sức mạnh của quy trình xác định (Workflow) với năng lực suy luận tự trị (Agent) và truy xuất tri thức (RAG).
4. **Bảo Mật Cấp Doanh Nghiệp (Enterprise-Grade Security):** Che giấu PII tại chỗ, kiểm tra SSRF nghiêm ngặt, cách ly đa người thuê tuyệt đối (No Tenant Bleed).
