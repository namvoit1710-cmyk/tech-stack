# Tech-Stack Knowledge Hub

> **Kho lưu trữ tài liệu kiến trúc, thiết kế kỹ thuật và các nền tảng công nghệ (Architecture & Tech-Stack Documentation).**

Chào mừng bạn đến với **Tech-Stack Knowledge Hub**. Đây là nơi tổng hợp các tài liệu phân tích kỹ thuật, sơ đồ kiến trúc, giải pháp thiết kế hệ thống và tiêu chuẩn công nghệ cho các phân hệ phần mềm hiện đại của SimpleMDG.

---

## 📂 Danh Mục Các Phân Hệ Công Nghệ (Tech Stacks)

| Phân Hệ / Dự Án | Lĩnh Vực & Công Nghệ Cốt Lõi | Tài Liệu Chi Tiết |
|---|---|---|
| **AI Workflow Management** | Event-Driven Orchestration, Clean Architecture 4 Lớp, FastAPI, Socket.IO, gRPC, 21 Node Types, Worker SDK, Kafka, SAP HANA | 📖 [Xem Tài Liệu](./docs/ai-workflow-management/) |
| **AI Agent Ecosystem** | Autonomous AI Agents, LangGraph, Multi-Agent Orchestration, Agent SDK, HITL Checkpointing, SAP BTP, LaidonLLM Gateway | 📖 [Xem Tài Liệu](./docs/agent/) |
| *(Mở Rộng Trong Tương Lai)* | *RAG System, Data Migration Platform, Knowledge Graph, SAP Integration Engine...* | *(Đang cập nhật)* |

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
- 🏛️ **[01. Kiến Trúc Tổng Thể](./docs/agent/01-architecture/):**
  - [Tổng Quan & So Sánh: Workflow vs Agent](./docs/agent/01-architecture/01-overview-and-philosophy.md)
  - [Topology Hệ Thống Đa Tác Nhân Phân Cấp](./docs/agent/01-architecture/02-agent-topology.md)
  - [Kiến Trúc Phân Tầng Clean Architecture Trong Agent SDK](./docs/agent/01-architecture/03-clean-architecture-4-layers.md)
- 🛠️ **[02. Bộ Công Cụ Phát Triển Agent SDK](./docs/agent/02-agent-sdk/):**
  - [4 Bộ Dựng Đồ Thị: Tool, Graph, Flow, SubGraph](./docs/agent/02-agent-sdk/01-graph-builders.md)
  - [Lưu Trữ Điểm Phục Hồi HANA Checkpointing & Tương Tác Con Người (HITL)](./docs/agent/02-agent-sdk/02-checkpointing-and-hitl.md)
  - [Quản Lý Ngân Sách Ngữ Cảnh (Context Budget) & 5 Chiến Lược Nén](./docs/agent/02-agent-sdk/03-context-budget-management.md)
  - [Dual Transports (SERVER vs CONSUMER) & Vòng Đời Sự Kiện 2 Tầng](./docs/agent/02-agent-sdk/04-transports-and-events.md)
- 👑 **[03. Bộ Điều Phối Hội Thoại Đa Tác Nhân (Orchestrator)](./docs/agent/03-orchestrator/):**
  - [Kiến Trúc 6 Tầng: Guardrails, Intent Classifier, Planner, JourneyTracker](./docs/agent/03-orchestrator/01-orchestrator-architecture.md)
  - [Cơ Chế Phối Hợp Đa Tác Nhân: AgentSelector, AgentCallCoordinator, Hand-off](./docs/agent/03-orchestrator/02-multi-agent-coordination.md)
- 🎯 **[04. Các Tác Nhân Nghiệp Vụ Chuyên Biệt (Domain Agents)](./docs/agent/04-domain-agents/):**
  - [Workflow Designer Agent: Tự động thiết kế quy trình AI Workflow](./docs/agent/04-domain-agents/01-workflow-designer-agent.md)
  - [Governance Schema Agent & Validation Rule Agent: Quản trị cấu trúc & sinh luật dữ liệu](./docs/agent/04-domain-agents/02-governance-and-validation-agents.md)
  - [Data & File Agents: Xử lý tệp đa định dạng, CSV tự chữa lành & Data Profiling](./docs/agent/04-domain-agents/03-data-and-file-agents.md)
  - [Troubleshooting Agent, Business Agent & Cổng Kết Nối LaidonLLM Gateway](./docs/agent/04-domain-agents/04-business-and-troubleshooting-agents.md)

---

## 🛠️ Nguyên Tắc Thiết Kế Cốt Lõi (Core Principles)

1. **Clean Architecture:** Tách biệt tuyệt đối giữa tầng nghiệp vụ Domain/Application và tầng hạ tầng Frameworks/Drivers.
2. **Deterministic vs Autonomous Synergy:** Kết hợp sức mạnh của quy trình xác định (Workflow) với năng lực suy luận tự trị (Agent).
3. **Event Sourcing & Replayability:** Nhật ký sự kiện (Event Log) và Checkpoint là chân lý, mọi trạng thái hệ thống đều có thể tái hiện chính xác.
4. **Mở Rộng Phân Tán (Horizontal Scalability):** Toàn bộ các dịch vụ đều thiết kế dạng stateless hoặc checkpoint-backed, scale độc lập theo nhu cầu.
