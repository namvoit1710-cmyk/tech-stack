# 02. Phát Hiện Lệch Cấu Trúc & Giám Sát (Schema Drift & Audit)

> **Phân hệ:** BI Dashboard & Analytics Platform  
> **Chủ đề:** Tự động phát hiện thay đổi schema nguồn (Schema Drift Detection), hàng đợi thư chết Dead-Letter Queue, và nhật ký kiểm toán (Audit Trail).

---

## 1. Tự Động Phát Hiện Lệch Cấu Trúc (Schema Drift Detection)

Khi các ứng dụng nguồn (SAP, CRM, Webhooks) nâng cấp phiên bản, cấu trúc bảng của chúng thường xuyên thay đổi:
- Thêm cột mới (ví dụ: bổ sung mã giảm giá `DISCOUNT_CODE`).
- Mở rộng độ dài ký tự (ví dụ: `CUSTOMER_NAME` từ 50 lên 100 ký tự).
- Xóa bỏ hoặc đổi tên trường dữ liệu.

Module `schema_drift_alert` (`app/layer1_domain/entities/schema_drift_alert.py`) tự động phân loại mức độ rủi ro:

```mermaid
flowchart TD
    SOURCE_CHANGE["Phát Hiện Thay Đổi Schema Nguồn"] --> EVAL{"Phân Loại Mức Độ Biến Đổi (Drift Level)"}
    
    EVAL -->|Thêm Cột Mới (SAFE_ADD)| AUTO_EXPAND["1. An Toàn: Tự Động Thêm Cột Vào Satellite / Dimension"]
    EVAL -->|Mở Rộng Kích Thước (TYPE_WIDEN)| AUTO_ALTER["2. An Toàn: Tự Động Chạy ALTER TABLE Mở Rộng Cột"]
    EVAL -->|Xóa Cột Hoặc Xung Đột Kiểu (BREAKING)| ALERT["3. Nguy Hiểm: Bắn Cảnh Báo SchemaDriftAlert Tới Admin"]
    
    AUTO_EXPAND --> CONTINUE["Pipeline Tiếp Tục Chạy Thông Suốt 100%"]
    AUTO_ALTER --> CONTINUE
    ALERT --> DLQ["Đưa Các Bản Ghi Lỗi Vào Dead-Letter Queue & Chờ Xử Lý"]
```

---

## 2. Hàng Đợi Thư Chết: Dead-Letter Queue (`dead_letter.py`)

Trong quá trình nạp dữ liệu hàng loạt từ tệp CSV hoặc CDC events:
- Nếu 999,900 bản ghi hợp lệ nhưng có 100 bản ghi bị lỗi định dạng ngày tháng hoặc sai kiểu số:
  - **Không làm hỏng toàn bộ mẻ nạp (No Full Batch Abortion):** 999,900 bản ghi tốt vẫn được nạp thành công vào Data Vault và Star Schema.
  - 100 bản ghi lỗi được tự động chuyển hướng vào bảng **`RAG_DEAD_LETTER_QUEUE`** kèm thông báo lỗi chi tiết (`ERROR_REASON`).
  - Cho phép quản trị viên xem danh sách lỗi, sửa trực tiếp trên giao diện và kích hoạt nút **Retry Dead Letters** để nạp bù mà không cần chạy lại toàn bộ mẻ từ đầu.

---

## 3. Nhật Ký Kiểm Toán Toàn Diện: Audit Trail (`audit_log.py`)

Tuân thủ các tiêu chuẩn bảo mật doanh nghiệp nghiêm ngặt (SOX, ISO 27001):
- Mọi hành vi trên hệ thống đều được ghi vết tự động:
  - Ai đã xem Dashboard tài chính lúc mấy giờ?
  - Ai đã thực hiện xuất báo cáo ra file Excel kèm bộ lọc nào?
  - Ai đã chỉnh sửa công thức tính toán của một chỉ số KPI?
- Nhật ký kiểm toán được lưu trữ bất biến (Append-Only) trong bảng `BI_AUDIT_LOGS`, sẵn sàng cho các đợt thanh tra bảo mật.
