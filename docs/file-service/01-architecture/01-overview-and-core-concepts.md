# 01. Tổng Quan & Các Khái Niệm Cốt Lõi (Overview & Core Concepts)

> **Phân hệ:** File Service  
> **Chủ đề:** Mục đích kiến trúc, quản lý phiên bản tệp (Versioning), mô hình lưu trữ Raw vs Processed, và kiểm soát xung đột lạc quan (OCC).

---

## 1. Bối Cảnh & Vai Trò Của File Service

Trong hệ sinh thái SimpleMDG (bao gồm AI Workflow, Agent, HANA RAG, và BI Dashboard), tệp tin là phương tiện trao đổi dữ liệu chủ đạo:
- Bảng tính Excel/CSV chứa hàng trăm ngàn dòng dữ liệu di chuyển (Data Migration).
- Tài liệu hợp đồng PDF/Word phục vụ trích xuất tri thức cho RAG.
- File đính kèm phê duyệt trong các workflow doanh nghiệp.

Nếu các dịch vụ tự lưu tệp cục bộ trên container:
- Dữ liệu sẽ biến mất khi container restart hoặc pod bị scale down (Ephemeral storage).
- Không có cơ chế quản lý phiên bản (Versioning), dẫn đến tình trạng ghi đè mất dữ liệu khi nhiều người dùng cùng thao tác.
- Tải tệp lớn làm nghẽn băng thông của máy chủ API chính.

**File Service** (`apps/backend/file`) ra đời như một **nền tảng lưu trữ đối tượng phân tầng (Tiered Object Storage)** chuyên dụng, cung cấp API quản lý tệp bất biến, quản lý phiên bản, và phân phối tải thông minh.

---

## 2. Các Khái Niệm Cốt Lõi (Core Concepts)

### 2.1. Định Danh Tệp Logic vs. Phiên Bản Cụ Thể (`file_id` vs `version_id`)
Hệ thống phân tách rạch ròi giữa tệp logic và phiên bản vật lý:
- **`file_id`:** Định danh logic cố định duy nhất đại diện cho tài liệu (ví dụ: `file_019dda5c-...`). Mã này **không bao giờ thay đổi** trong suốt vòng đời của tệp.
- **`version_id`:** Định danh duy nhất cho từng lần tải lên cụ thể (ví dụ: `ver_019dda5d-...`). Mỗi lần người dùng cập nhật tệp, một `version_id` mới được sinh ra.

```
Tệp Logic: Danh_sach_khach_hang.csv (file_id = "file_019dda5c-...")
   │
   ├── Phiên bản 1 (ver_019dda5d-...) - Upload lúc 08:00 bởi User A (10,000 dòng)
   ├── Phiên bản 2 (ver_019dda5e-...) - Upload lúc 10:30 bởi User B (10,500 dòng)
   └── Phiên bản 3 (ver_019dda5f-...) - Upload lúc 14:15 bởi User A (11,000 dòng)
```

---

### 2.2. Trạng Thái Phiên Bản: `current_version_id` vs `latest_version_number`
- **`latest_version_number`:** Bộ đếm phiên bản số học cao nhất vừa được cấp phát (ví dụ: Version 3).
- **`current_version_id`:** Phiên bản đang ở trạng thái **Sẵn Sàng (READY)** và được phục vụ tải về mặc định.
- **Tính năng Zero-Downtime Upload:** Khi người dùng đang upload phiên bản 3 (dung lượng 500MB đang được xử lý hoặc quét virus), `current_version_id` vẫn trỏ về Phiên bản 2. Người dùng khác tải tệp vẫn nhận được dữ liệu hoàn chỉnh, không bao giờ gặp lỗi tệp tải dở dang!

---

### 2.3. Mô Hình Lưu Trữ Kép: Nguyên Bản vs Đã Xử Lý (Raw vs. Processed)
Mỗi phiên bản tệp được lưu giữ dưới hai khóa định danh lưu trữ độc lập:
1. **`raw_storage_key`:** Đối tượng tệp gốc nguyên bản 100% do người dùng tải lên, được bảo toàn nguyên vẹn phục vụ mục đích kiểm toán và đối chiếu pháp lý.
2. **`processed_storage_key`:** Đối tượng tệp sau khi đã được hệ thống chuẩn hóa (ví dụ: với file CSV, hệ thống tự động bổ sung cột định danh duy nhất `row_id`). Toàn bộ các dịch vụ downstream (BI, RAG, Migration) mặc định tải và đọc phiên bản đã chuẩn hóa này.

---

### 2.4. Kiểm Soát Xung Đột Lạc Quan (Optimistic Concurrency Control - OCC)
Để chống hiện tượng ghi đè mất mát dữ liệu (Lost Update Problem) khi nhiều người dùng hoặc tiến trình tự động cùng upload lên một `file_id`:
- Khi tải lên phiên bản mới, client gửi kèm cờ `previous_version_id`:
  ```http
  POST /api/v1/upload?file_id=file_123&previous_version_id=ver_001
  ```
- Nếu trong lúc đó có người khác đã tải lên `ver_002`, hệ thống lập tức từ chối với mã lỗi **HTTP 409 Conflict**:
  ```json
  {
    "error": "VERSION_CONFLICT",
    "message": "File đã bị sửa đổi bởi người khác. Phiên bản hiện tại là ver_002."
  }
  ```
- Client buộc phải tải phiên bản mới nhất về hợp nhất trước khi upload tiếp.
