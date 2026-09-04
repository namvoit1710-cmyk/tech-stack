# 02. Topology Hệ Thống & Cổng Giao Tiếp (System Topology)

> **Phân hệ:** AI Eagle Platform  
> **Chủ đề:** Bản đồ liên kết dịch vụ, các bảng `AE_*` trên SAP HANA, và giao tiếp với các vi dịch vụ trong hệ sinh thái SimpleMDG.

---

## 1. Sơ Đồ Topology Mạng Toàn Diện

```mermaid
flowchart TB
    subgraph CALLERS["Các Hệ Thống Tiêu Thụ Dịch Vụ"]
        MDG_CORE["SimpleMDG Master Data Portal"]
        WORKFLOW_SYS["AI Workflow Management (:8001)"]
        AGENT_SYS["AI Agent Subsystem (:8002)"]
        ADMIN_USER["Chuyên Viên Quản Trị Dữ Liệu (UI Console)"]
    end

    subgraph EAGLE_APP["Hệ Thống AI Eagle Service"]
        GOV_API["governance-smart-api (:8080)<br/>Cổng Nhập Dữ Liệu & UI Console Quản Trị"]
        SDK_CORE["smart-service-sdk (:8088)<br/>Động Cơ So Khớp Trùng Lặp Lai & Phân Tích SDS"]
    end

    subgraph AI_PROVIDERS["Các Dịch Vụ AI & Nhúng Ngôn Ngữ"]
        OPENAI["OpenAI / Azure OpenAI (LLM Term Expansion)"]
        LOCAL_EMB["Local Embedding Provider (FastEmbed bge-small-en-v1.5)"]
        SPACY_NLP["spaCy Dependency Graph Extractor"]
    end

    subgraph HANA_STORAGE["Hạ Tầng SAP HANA In-Memory Database (:39041)"]
        T_CHUNKS[("AE_RAG_CHUNKS<br/>REAL_VECTOR(640)")]
        T_GRAPH[("AE_GRAPH_WORKSPACE<br/>Vertices: AE_RAG_GRAPH_ENTITIES<br/>Edges: AE_RAG_GRAPH_RELATIONS")]
        T_JOBS[("AE_RAG_BACKGROUND_JOBS & AE_RAG_CHECK_RESULTS")]
        T_CONFIG[("AE_SERVICE_CONFIGURATIONS (Ngưỡng Mờ, Trọng Số Đồ Thị)")]
    end

    subgraph EXT_STORAGE["Hệ Thống Lưu Trữ Tệp"]
        FILE_SERVICE["File Service (:8000)<br/>Lấy Tệp Nguồn Nhập Liệu"]
    end

    CALLERS <-->|"REST API / HTTP JSON"| GOV_API
    GOV_API -->|"In-Process Dependency Injection"| SDK_CORE

    SDK_CORE --> OPENAI
    SDK_CORE --> LOCAL_EMB
    SDK_CORE --> SPACY_NLP

    SDK_CORE --> T_CHUNKS
    SDK_CORE --> T_GRAPH
    SDK_CORE --> T_JOBS
    SDK_CORE --> T_CONFIG

    SDK_CORE <-->|"Tải Tệp Import"| FILE_SERVICE
```

---

## 2. Các Cổng Giao Tiếp & Biến Môi Trường

| Thành Phần | Cổng Mặc Định | Biến Cấu Hình | Ghi Chú |
|---|---|---|---|
| **Governance Smart API** | `8080` (hoặc CloudFoundry `$PORT`) | `PORT=8080`, `HOST=0.0.0.0` | Cổng HTTP tiếp nhận tệp tải lên, import data và UI console. |
| **Smart Service SDK API** | `8088` (chạy độc lập) | `PORT=8088` | Cung cấp toàn bộ các API `/api/v1/duplicate-check`, `/similarity`, `/search`. |
| **SAP HANA Database** | `39041` (hoặc `443`) | `HANA_ADDRESS`, `HANA_PORT`, `HANA_USER`, `HANA_PASSWORD` | Lưu trữ chỉ mục vector, đồ thị tri thức và cấu hình runtime. |
| **OpenAI / Azure Gateway** | HTTPS | `OPENAI_API_KEY`, `OPENAI_BASE_URL` | Phục vụ mở rộng từ khóa ngữ nghĩa và phân tích nâng cao. |
| **File Service Client** | `8000` | `FILE_SERVICE_BASE_URL` | Kết nối vi dịch vụ tệp để tải các file import lớn. |

---

## 3. Kiến Trúc Tiến Trình Đồng Nhất (In-Process Runtime Composition)

Điểm đặc sắc trong thiết kế của AI Eagle là:
- `governance-smart-api` không gọi sang `smart-service-sdk` qua mạng HTTP trung gian (tránh tốn độ trễ mạng).
- Thay vào đó, nó **nhúng trực tiếp SDK vào cùng một tiến trình FastAPI** (`smart_create_app(container_enricher=...)`).
- Kết quả: Toàn bộ Use Cases và Repository của cả hai thành phần đều chia sẻ chung một Dependency Container và Connection Pool SAP HANA duy nhất trong RAM, mang lại tốc độ thực thi tối đa.
