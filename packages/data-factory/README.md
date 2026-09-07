# Data Factory Core Engine

> **Mã nguồn lõi của Data Factory — Động cơ chuyển đổi, xác thực chất lượng và di chuyển dữ liệu lớn (Data Migration, Transformation & Validation Engine)**

Thư mục này chứa mã nguồn hoàn chỉnh của phân hệ **Data Factory** được xây dựng theo chuẩn Clean Architecture 4 lớp.

---

## 🌟 Các Tính Năng & Năng Lực Cốt Lõi

1. **Data Migration Pipeline (`POST /api/v1/data-migration/execute`):**
   - Di chuyển và xác thực hàng triệu bản ghi trực tiếp từ các bảng ảo SAP HANA (HANA Virtual Tables như `STAGING_VT_MARA`).
   - Phản hồi **202 Accepted** ngay tức thì kèm khóa Idempotency (`job_id`).
   - Xuất dữ liệu thẳng vào 2 bảng vật lý trong SAP HANA: `DF_REPORT_<job_id>` (báo cáo vi phạm chi tiết) và `DF_CB_<job_id>` (dữ liệu kết quả sạch).
   - Truyền phát tiến độ thời gian thực qua **Server-Sent Events (SSE)** tại `/api/v1/data-migration/jobs/{id}/events`.
   - Bắn Webhook callback tới Integration Hub khi hoàn tất.
2. **Bộ Máy Xác Thực Dữ Liệu Khổng Lồ (`PolarsValidatorProvider`):**
   - Hỗ trợ hơn 126 quy tắc sản xuất (Production Rules): `required`, `pattern`, `range`, `length`, `format`, `lookup`, `cross_field`, `composite_unique`, `semantic_unique`.
   - **Rule Difficulty Router:** Tự động phân loại quy tắc (Simple vs Medium vs Hard) để tối ưu hóa đường đi: Simple chạy in-memory / SQL Pushdown, Hard chạy qua Expression Engine.
3. **Bộ Máy Chuyển Đổi Dữ Liệu Đa Chiều (`PolarsTransformerProvider` & `PolarsRowTransformerProvider`):**
   - Biến đổi dữ liệu theo hàng (Row transformation) và theo cột (Column transformation).
   - Regex replace, công thức tính toán an toàn (`safe_expression`), tra cứu từ điển (Lookup mapping), xử lý ngày tháng và giá trị rỗng.
4. **Adaptive Batching & Nhận Diện Bộ Nhớ Container (`adaptive_batching.py`):**
   - Đọc trực tiếp cgroups v1 & v2 (`/sys/fs/cgroup/memory.stat`).
   - Tự động điều chỉnh kích thước mẻ (Batch Size) theo số byte ước tính mỗi dòng và RAM khả dụng để chống tràn bộ nhớ (Kubernetes OOM-Killed).

---

## 🚀 Hướng Dẫn Cài Đặt & Chạy Nhanh

```bash
cd packages/data-factory
pip install -r requirements.txt
python main.py
```
- Server chạy tại: `http://0.0.0.0:8000`
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
