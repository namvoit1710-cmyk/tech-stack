# 01. Điều Phối Tác Vụ & Tính Bất Biến (Job Dispatch & Idempotency)

> **Phân hệ:** Data Factory Platform  
> **Chủ đề:** Khởi tạo tác vụ di chuyển dữ liệu qua `POST /api/v1/data-migration/execute`, phản hồi 202 Accepted và cơ chế chống lặp (Idempotency).

---

## 1. Khởi Tạo Tác Vụ Di Chuyển Dữ Liệu (Job Dispatch)

Khi tiến trình di chuyển dữ liệu tổng thể kích hoạt, Integration Hub gửi một yêu cầu dispatch tới Data Factory:

```http
POST /api/v1/data-migration/execute
Content-Type: application/json

{
  "job_id": "EDAD7E50B12D454586BEB49F81CE2151",
  "source": {
    "kind": "hana_virtual_table",
    "connection": {
      "host": "hana.prod.hanacloud.ondemand.com",
      "port": 443,
      "user": "USR_MIGRATION",
      "schema": "USR_MIGRATION",
      "encrypt": true
    },
    "credential": {
      "scheme": "resolve-token",
      "token": "c34340d2-token-uuid",
      "resolve_url": "http://ih-host/api/v1/data-migration/connection-secret"
    },
    "virtual_table": "STAGING_VT_MARA"
  },
  "rules": [
    { "type": "required", "field": "MATNR", "error_message": "Mã vật tư bắt buộc" },
    { "type": "pattern", "field": "MATNR", "params": { "regex": "^[A-Z0-9]{8,18}$" } }
  ],
  "callback": {
    "url": "http://ih-host/api/v1/data-migration/df-callback",
    "job_id": "EDAD7E50B12D454586BEB49F81CE2151"
  }
}
```

---

## 2. Tính Bất Biến Của `job_id` (Idempotency Guarantee)

Trong các hệ thống phân tán, sự cố mạng chập chờn có thể khiến client gửi lại (Retry) cùng một request nhiều lần.

**Quy tắc bất biến của `job_id`:**
- Nếu một request gửi tới mang `job_id` **đã tồn tại trong hệ thống**:
  - Data Factory **tuyệt đối không chạy lại** mẻ di chuyển dữ liệu.
  - Hệ thống trả về nguyên vẹn trạng thái hiện tại của job đó (`rowsRead`, `rowsPassed`, `progressPct`).
- **Tại sao điều này mang tính sống còn?**
  - Trường `credential.token` chỉ có giá trị sử dụng **1 lần duy nhất (Single-use)**.
  - Nếu Data Factory cố gắng chạy lại mẻ, việc gọi resolve-token lần 2 sẽ bị Integration Hub từ chối với lỗi `403 Forbidden`, dẫn đến toàn bộ mẻ di chuyển bị treo!

---

## 3. Phản Hồi Ngay Lập Tức: HTTP 202 Accepted

Data Factory không bắt client phải treo kết nối chờ đợi:
- Ngay sau khi thẩm định cú pháp payload và ghi nhận `job_id`, hệ thống trả về mã **`202 Accepted`** chỉ trong vòng **dưới 50 mili-giây**:

```json
{
  "jobId": "EDAD7E50B12D454586BEB49F81CE2151",
  "status": "ACCEPTED",
  "reportTable": "DF_REPORT_EDAD7E50B12D454586BEB49F81CE2151",
  "callbackTable": "DF_CB_EDAD7E50B12D454586BEB49F81CE2151",
  "rowsRead": 0,
  "rowsPassed": 0,
  "rowsWritten": 0,
  "progressPct": 0.0,
  "rulesApplied": 0,
  "rulesViolated": 0,
  "elapsedSec": 0.0
}
```

Caller biết trước tên của 2 bảng kết quả (`reportTable` và `callbackTable`) ngay từ phản hồi 202 này để chuẩn bị các bước kế tiếp.
