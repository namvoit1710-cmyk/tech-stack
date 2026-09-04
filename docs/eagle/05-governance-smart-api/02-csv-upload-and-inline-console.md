# 02. Nạp Tệp Trực Tiếp & Giao Diện Quản Trị (Upload & UI Console)

> **Phân hệ:** AI Eagle Platform  
> **Chủ đề:** Nạp tệp CSV trực tiếp trên Governance Smart API và giao diện web console nội bộ (`/api/v1/ui`).

---

## 1. Nạp Tệp CSV Trực Tiếp (Direct CSV Drag-and-Drop)

Bên cạnh luồng import thông qua File Service, `governance-smart-api` cung cấp một đường dẫn nạp tệp trực tiếp phục vụ cho việc kiểm thử nhanh hoặc môi trường cục bộ:
- Người dùng có thể kéo thả trực tiếp một tệp CSV chứa dữ liệu khách hàng.
- Use case `upload_import_file_usecase` tiếp nhận luồng tệp:
  1. Phân tích nội dung CSV thành các dòng dữ liệu JSON có cấu trúc.
  2. Lưu trữ kết quả phân tích vào repository kết quả dùng chung (`shared_result_repository`).
  3. Trả về kết quả xem trước ngay trên giao diện (Inline Preview).

---

## 2. Giao Diện Quản Trị Web Tích Hợp Sẵn (In-App UI Console)

Nhằm giúp các kỹ sư dữ liệu và chuyên viên quản trị không phải dùng curl hoặc Postman để kiểm tra hệ thống, AI Eagle tích hợp sẵn một **Web UI Console** trực quan ngay trong mã nguồn (`/api/v1/ui`):

```
http://127.0.0.1:8080/ui
```

### Các Màn Hình Chức Năng Trên Console:

| Đường Dẫn URL | Màn Hình Chức Năng | Tính Năng Nổi Bật |
|---|---|---|
| `/ui` | **Trang Chủ Bảng Điều Khiển** | Tổng quan trạng thái dịch vụ, số lượng bản ghi chỉ mục, và các tác vụ import gần nhất. |
| `/ui/search` | **Tra Cứu Tương Đồng (Search Console)** | Cho phép gõ từ khóa tìm kiếm thử nghiệm, xem danh sách ứng viên và điểm số tương đồng. |
| `/ui/similarity` | **Kiểm Tra Trùng Lặp Trực Quan** | Nhập form thông tin đối tác (Mã số thuế, Tên, Địa chỉ), chọn luật so khớp và xem cây suy luận **Decision Trace**. |
| `/ui/config` | **Cấu Hình Tham Số Runtime** | Điều chỉnh trực tiếp ngưỡng mờ `SEARCH_FUZZY_THRESHOLD`, điểm vector `VECTOR_MIN_SCORE` trên giao diện. |
| `/ui/material-sds-analysis` | **Phân Tích Hóa Chất SDS** | Tải lên tài liệu SDS dạng PDF hoặc văn bản để kiểm tra khả năng bóc tách thành phần nguy hại và mã GHS. |

---

## 3. Ý Nghĩa Thực Tiễn Của UI Console

- **Dành Cho Khách Hàng & Demo:** Cho phép trình diễn (Demo) ngay lập tức năng lực AI của SimpleMDG cho khách hàng doanh nghiệp mà không cần phụ thuộc vào việc dựng toàn bộ giao diện Frontend phức tạp.
- **Dành Cho Kiểm Thử & Tinh Chỉnh (Tuning):** Kỹ sư dữ liệu có thể thử nghiệm các ngưỡng so khớp mờ khác nhau (từ 0.70 đến 0.90) trên tập dữ liệu thực tế để tìm ra cấu hình tối ưu nhất cho từng khách hàng.
