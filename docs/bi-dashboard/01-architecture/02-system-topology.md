# 02. Topology Hệ Thống & Cổng Giao Tiếp (System Topology)

> **Phân hệ:** BI Dashboard & Analytics Platform  
> **Chủ đề:** Bản đồ liên kết hệ thống, giao tiếp giữa Backend FastAPI và Frontend React 19, tích hợp SAP HANA và XSUAA.

---

## 1. Sơ Đồ Topology Tổng Thể

```mermaid
flowchart TB
    subgraph UI_TIER["Frontend Tier: Dashboard AI (:3000 / :3004)"]
        REACT_APP["React 19 SPA (Rsbuild · Module Federation)"]
        DND_CANVAS["Drag & Drop Canvas Studio (@dnd-kit)"]
        RECHARTS_VIEW["Dynamic Visualizations (Recharts)"]
        CHAT_COPILOT["AI Insight Chat Copilot (@ldc/chat-sdk)"]
    end

    subgraph API_TIER["Backend Tier: BI Dashboard Service (:8001)"]
        FASTAPI["FastAPI Engine (/api/v1/olap)"]
        INGESTION["L0/L1 Ingestion Engine (Polars & Streaming)"]
        VAULT_ENGINE["Data Vault 2.0 Automation Engine"]
        STAR_ENGINE["Star Schema Manifest & SQL Composer"]
        RLS_MASKING["Governance, RLS & Column Masking Guard"]
    end

    subgraph AUTH_TIER["Xác Thực & Bảo Mật Doanh Nghiệp"]
        XSUAA["SAP BTP XSUAA / simplemdg_auth<br/>(JWT Verification · Role Mapping · Tenant Match)"]
    end

    subgraph STORAGE_TIER["Hạ Tầng Dữ Liệu Bền Vững (SAP HANA :39041)"]
        H_STAGE[("HANA Schemas: STAGING<br/>Virtual Tables · Raw Files")]
        H_VAULT[("HANA Schemas: DATA VAULT<br/>Hubs · Links · Satellites · PIT Tables")]
        H_STAR[("HANA Schemas: OLAP STAR<br/>Fact Tables · Conformed Dimensions")]
        H_META[("HANA Schemas: METADATA & CONFIG<br/>Dashboards · Charts · Access Rules")]
    end

    subgraph EVENT_TIER["Hàng Đợi Sự Kiện & CDC"]
        EVENT_BUS[["Kafka / SAP Event Mesh<br/>(Change Events · Snapshot Requests)"]]
    end

    UI_TIER <-->|"REST API / Bearer Token"| FASTAPI
    UI_TIER -.->|"Auth Flow"| XSUAA
    FASTAPI --> XSUAA

    FASTAPI --> INGESTION
    FASTAPI --> VAULT_ENGINE
    FASTAPI --> STAR_ENGINE
    FASTAPI --> RLS_MASKING

    INGESTION --> H_STAGE
    VAULT_ENGINE --> H_VAULT
    STAR_ENGINE --> H_STAR
    RLS_MASKING --> H_META

    FASTAPI --> EVENT_BUS
```

---

## 2. Các Cổng Giao Tiếp & Quy Chuẩn Định Tuyến

| Thành Phần | Cổng Mặc Định | Giao Thức / Endpoint Gốc | Trách Nhiệm Chính |
|---|---|---|---|
| **Frontend Dashboard AI** | `3000` (hoặc `3004`) | HTTP / Module Federation | Giao diện kéo thả canvas, hiển thị biểu đồ, bộ lọc chéo (Cross-filters). |
| **Backend BI Dashboard** | `8001` | HTTP `/api/v1/olap` | Cung cấp toàn bộ 134 endpoints cho Ingestion, Vault, Star Schema, Charts và Dashboards. |
| **SAP HANA Database** | `39041` (hoặc `443` Cloud) | SQL / `hdbcli` Connection Pool | Kho lưu trữ in-memory duy nhất cho cả 3 tầng dữ liệu (Staging, Vault, Star). |
| **SAP XSUAA Service** | Cloud HTTPS | OAuth2 / JWT Tokens | Cung cấp token xác thực phân quyền theo vai trò (`ADMIN`, `ANALYST`, `VIEWER`). |

---

## 3. Xác Thực & Đa Người Thuê (Multi-Tenancy)

- Hệ thống sử dụng thư viện nội bộ **`simplemdg_auth`** tích hợp trực tiếp với SAP BTP XSUAA.
- **Xác thực theo từng Router (Per-Router Auth):**
  - Giúp phân định rành mạch các route công khai (Health checks, doc specs) và các route nghiệp vụ bắt buộc phải có Bearer Token.
- **Nguyên tắc `enforce_tenant_match`:**
  - Token JWT mang thông tin `tenant_id` của phiên làm việc.
  - Nếu payload trong request body hoặc query parameter cố tình truyền một `tenant_id` khác với token, hệ thống lập tức từ chối với mã lỗi `403 Forbidden`.
