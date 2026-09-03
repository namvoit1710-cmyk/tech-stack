# 02. Topology Hệ Thống & Cổng Giao Tiếp (System Topology)

> **Phân hệ:** File Service  
> **Chủ đề:** Bản đồ liên kết dịch vụ, các tầng lưu trữ đối tượng, và giao tiếp giữa các vi dịch vụ trong hệ sinh thái.

---

## 1. Sơ Đồ Topology Mạng Toàn Diện

File Service đóng vai trò là xương sống trung chuyển và lưu trữ tệp cho toàn bộ nền tảng SimpleMDG:

```mermaid
flowchart TB
    subgraph CLIENTS["Khách Hàng & Các Vi Dịch Vụ Nội Bộ"]
        UI_CLIENT["Trình Duyệt Người Dùng (Web Upload / Download)"]
        WF_SERVICE["AI Workflow Management (:8001)"]
        AGENT_SERVICE["AI Agent Subsystem (:8002)"]
        RAG_SERVICE["HANA RAG Service (:8000)"]
        BI_SERVICE["BI Dashboard Service (:8001)"]
    end

    subgraph API_GATEWAY["Cổng Dịch Vụ Tệp (File Service :8000)"]
        FASTAPI_APP["FastAPI Application Engine<br/>(/api/v1/upload · /api/v1/download · /api/v1/multipart)"]
        PERF_MONITOR["Performance Monitor Engine<br/>(Daily Rotating JSONL Logs)"]
    end

    subgraph STORAGE_TIERS["Hệ Thống Lưu Trữ Đối Tượng Phân Tầng (Tiered Storage)"]
        HOT_TIER[("Tầng Nóng (Hot Tier): Local SSD / NVMe<br/>(Ghi Ngay Tức Thì · Cache Đọc Tốc Độ Cao)")]
        COLD_TIER[("Tầng Lạnh/Ấm (Warm/Cold Tier): S3 / SeaweedFS<br/>(Lưu Trữ Bền Vững Quy Mô Lớn · MinIO)")]
    end

    subgraph METADATA_TIER["Hạ Tầng Quản Lý Metadata"]
        HANA_META[("SAP HANA Database (:39041)<br/>Bảng: FILES, FILE_VERSIONS, SESSIONS")]
        SQLITE_META[("SQLite (Môi Trường Dev Cục Bộ)")]
    end

    CLIENTS <-->|"REST API / Bearer Token"| FASTAPI_APP
    CLIENTS -.->|"Presigned URL (Tải Trực Tiếp)"| COLD_TIER

    FASTAPI_APP --> HOT_TIER
    HOT_TIER -.->|"Đồng Bộ Bất Đồng Bộ (Async Task)"| COLD_TIER
    
    FASTAPI_APP --> HANA_META
    FASTAPI_APP -.-> SQLITE_META
    FASTAPI_APP --> PERF_MONITOR
```

---

## 2. Các Cổng Giao Tiếp & Biến Môi Trường

| Thành Phần | Cổng Mặc Định | Biến Cấu Hình | Ghi Chú |
|---|---|---|---|
| **File Service REST API** | `8000` (hoặc `8003`) | `PORT=8000`, `HOST=0.0.0.0` | Cung cấp toàn bộ các API tải lên, tải xuống, tạo presigned URL. |
| **Local Storage Directory** | File System | `LOCAL_STORAGE_DIR=/data/storage` | Thư mục đĩa SSD dùng làm Hot Tier đệm. |
| **AWS S3 / S3-Compatible** | HTTPS / `443` | `STORAGE_TYPE=AWS_S3`, `BUCKET_NAME` | Kết nối AWS S3 hoặc MinIO qua `S3_ENDPOINT_URL`. |
| **SeaweedFS Storage** | `8333` (Filer) | `STORAGE_TYPE=SEAWEEDFS`, `SEAWEEDFS_ENDPOINT` | Hệ thống lưu trữ đối tượng phân tán mã nguồn mở hiệu năng cao. |
| **SAP HANA Metadata DB** | `39041` | `HANA_CONNECTION_STRING` | Lưu trữ siêu dữ liệu phiên bản tệp chuẩn ACID. |

---

## 3. Cơ Chế Đường Dẫn Tắt: Presigned URLs

Đối với các tệp dung lượng lớn (từ 50MB đến hàng gigabyte):
- Client không cần phải gửi toàn bộ luồng byte qua máy chủ File Service (tránh gây tắc nghẽn CPU và mạng của web server).
- **Quy trình Presigned URL:**
  1. Client gọi: `GET /api/v1/presigned-url/{file_id}`.
  2. File Service ký số một URL an toàn có thời hạn (ví dụ: 15 phút).
  3. Client tải trực tiếp dữ liệu lên/xuống thẳng kho lưu trữ S3 / SeaweedFS bằng URL đã ký đó.
