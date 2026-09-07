# 02. Topology Hệ Thống & Cổng Giao Tiếp (System Topology)

> **Phân hệ:** Data Factory Platform  
> **Chủ đề:** Bản đồ kết nối dịch vụ, tích hợp SAP HANA Virtual Tables, Integration Hub, và luồng dữ liệu hai chiều.

---

## 1. Sơ Đồ Topology Toàn Diện

```mermaid
flowchart TB
    subgraph CALLERS["Hệ Thống Điều Phối & Caller"]
        IH["Integration Hub (:8004)<br/>Bộ điều phối di chuyển dữ liệu"]
        WF["AI Workflow Management (:8001)<br/>Node Data Cleanse & Transform"]
        UI_APP["Migration Portal UI<br/>Lắng nghe tiến độ qua SSE"]
    end

    subgraph DATA_FACTORY["Nhà Máy Dữ Liệu: Data Factory (:8000)"]
        FASTAPI_APP["FastAPI Application Engine"]
        MIGRATION_ENG["Data Migration Execution Engine"]
        TRANSFORM_ENG["Polars Transformation Provider"]
        VALIDATE_ENG["Polars Validation Provider (126+ Rules)"]
        ADAPTIVE_MGR["Adaptive Batching & cgroups Monitor"]
    end

    subgraph STORAGE_TIER["Hệ Thống CSDL SAP HANA In-Memory (:39041)"]
        VIRTUAL_TABLES[("Bảng Nguồn Ảo (SDA):<br/>STAGING_VT_MARA / VT_KNA1")]
        DELIVERY_REPORT[("Bảng Báo Cáo Lỗi:<br/>DF_REPORT_<job_id>")]
        DELIVERY_CLEAN[("Bảng Dữ Liệu Sạch:<br/>DF_CB_<job_id>")]
    end

    subgraph FILE_SERVICE_TIER["Kho Lưu Trữ Tệp Đối Tượng"]
        FILE_SERVICE["File Service (:8000)<br/>Đọc/Ghi Tệp CSV, Excel, Parquet"]
    end

    IH -->|"1. Dispatch Task (POST /execute)"| FASTAPI_APP
    FASTAPI_APP --> MIGRATION_ENG
    FASTAPI_APP --> TRANSFORM_ENG
    FASTAPI_APP --> VALIDATE_ENG
    ADAPTIVE_MGR -.->|"Giám sát RAM container"| MIGRATION_ENG

    MIGRATION_ENG -->|"2. Đọc luồng mẻ lớn"| VIRTUAL_TABLES
    MIGRATION_ENG -->|"3. Ghi lỗi chi tiết"| DELIVERY_REPORT
    MIGRATION_ENG -->|"4. Ghi dữ liệu sạch"| DELIVERY_CLEAN

    FASTAPI_APP <-->|"Tải tệp lớn"| FILE_SERVICE

    FASTAPI_APP -.->|"5. SSE Streaming (/events)"| UI_APP
    MIGRATION_ENG -.->|"6. Webhook Callback Ack"| IH
```

---

## 2. Các Cổng Giao Tiếp & Biến Môi Trường Cốt Lõi

| Thành Phần | Cổng / Đường Dẫn | Biến Môi Trường | Chức Năng Chính |
|---|---|---|---|
| **Data Factory API** | `8000` | `PORT=8000`, `HOST=0.0.0.0` | Cổng HTTP tiếp nhận lệnh thực thi di chuyển và kiểm tra dữ liệu. |
| **File Server Base URL** | `8000` | `FILE_SERVER_URL` | Địa chỉ kết nối vi dịch vụ File Service để tải tệp. |
| **Rules Database** | File JSON / SQLite | `RULES_DB_PATH=validation_rules_db.json` | Nơi lưu trữ danh mục các quy tắc xác thực chuẩn. |
| **HANA Target Connection** | `39041` (hoặc Cloud `443`) | Dynamic qua payload request | Kết nối tới SAP HANA bằng thông tin `connection` trong body request. |

---

## 3. Cơ Chế Xác Thực Bằng Token Đơn Dụng (Single-Use Resolve-Token)

Để tránh việc gửi mật khẩu CSDL SAP HANA thô (`plaintext password`) qua mạng trong mỗi request:
- Caller (Integration Hub) gửi một trường `credential.token` dùng một lần (Single-use token).
- Data Factory gọi ngược lại endpoint bảo mật của Integration Hub (`resolve_url`) để đổi token lấy mật khẩu kết nối.
- Sau khi kết nối thành công tới SAP HANA, token này lập tức bị hủy bỏ (Expiring immediately), đảm bảo tính an toàn tối đa kể cả khi log request bị lộ.
