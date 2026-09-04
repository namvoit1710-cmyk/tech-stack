# 01. Nhập Dữ Liệu Chỉ Mục Ngầm (Request-Driven Duplicate Import)

> **Phân hệ:** AI Eagle Platform  
> **Chủ đề:** Tác vụ nhập dữ liệu chỉ mục ngầm qua `POST /governance/import-data`, theo dõi tiến độ qua `AE_RAG_BACKGROUND_JOBS`.

---

## 1. Vấn Đề Nhập Dữ Liệu Khối Lượng Lớn

Khi doanh nghiệp di chuyển hoặc khởi tạo hệ thống Master Data:
- Có hàng chục ngàn đến hàng triệu bản ghi khách hàng/nhà cung cấp cần được nạp vào chỉ mục so khớp.
- Quá trình tính toán embedding vector và trích xuất thực thể đồ thị cho 100,000 dòng có thể mất từ vài phút đến nửa tiếng.
- **Yêu cầu kỹ thuật:** Tuyệt đối không được để caller HTTP phải chờ đợi (tránh Timeout HTTP 504). Mọi tác vụ nạp dữ liệu lớn phải chạy dưới dạng **Tác Vụ Ngầm Bất Đồng Bộ (Background Job)**.

---

## 2. Quy Trình Vận Hành Của Tác Vụ Nhập Liệu

```mermaid
sequenceDiagram
    autonumber
    actor Client as Governance UI / Migration Service
    participant GOV as Governance Smart API (:8080)
    participant SDK as Smart Service SDK Background Job Coordinator
    participant FILE as File Service (:8000)
    participant HANA as SAP HANA Database (AE_* Tables)

    Client->>GOV: POST /governance/import-data<br/>{ file_ids: ["FILE_001", "FILE_002"], tenant_id: "tenant-1" }
    
    GOV->>SDK: Khởi tạo Background Job
    SDK->>HANA: INSERT INTO AE_RAG_BACKGROUND_JOBS (job_id, status='ACTIVE', started_at=NOW)
    SDK-->>GOV: Trả về job_id = "job-20260904-001"
    GOV-->>Client: 202 Accepted { "job_id": "job-20260904-001", "status": "ACTIVE" }

    rect rgb(240, 248, 255)
    Note over SDK,HANA: Tiến Trình Ngầm Thực Thi Độc Lập
    SDK->>FILE: Tải nội dung tệp theo file_id
    SDK->>SDK: Parse dòng dữ liệu & Sinh vector REAL_VECTOR(640)
    SDK->>SDK: Trích xuất thực thể & quan hệ đồ thị tri thức
    SDK->>HANA: Bulk Insert vào AE_RAG_CHUNKS & AE_RAG_GRAPH_*
    SDK->>HANA: UPDATE AE_RAG_BACKGROUND_JOBS SET status='COMPLETED', ended_at=NOW
    end

    loop Client Thăm Dò Tiến Độ (Polling)
        Client->>GOV: GET /api/v1/import-jobs/job-20260904-001
        GOV->>HANA: Đọc trạng thái từ AE_RAG_BACKGROUND_JOBS
        HANA-->>GOV: status = 'COMPLETED'
        GOV-->>Client: 200 OK { "status": "COMPLETED", "processed_records": 50000 }
    end
```

---

## 3. Khả Năng Khôi Phục & Quản Trị Lỗi

- **Lưu Vết Lỗi Chi Tiết:** Nếu có bất kỳ sự cố nào xảy ra trong quá trình nạp (ví dụ tệp CSV bị lỗi cú pháp dòng 1500), thông tin ngoại lệ được ghi nhận chi tiết vào cột `ERROR_MESSAGE` của bảng `AE_RAG_BACKGROUND_JOBS`.
- **An Toàn Đa Người Thuê (Tenant Isolation):** Mọi bản ghi được nạp vào đều được gán nhãn `TENANT_ID` cố định, ngăn chặn hoàn toàn hiện tượng dữ liệu chỉ mục của tenant này bị truy vấn bởi tenant khác.
