# 02. Các Tác Nhân Quản Trị Schema & Luật Dữ Liệu

> **Phân hệ:** AI Agent Subsystem  
> **Chủ đề:** Bộ đôi tác nhân quản trị dữ liệu cốt lõi: `governance-schema-agent` và `validation-rule-agent`.

---

## 1. `governance-schema-agent` (Quản Trị Schema Dữ Liệu Doanh Nghiệp)

### 1.1. Mục Tiêu Nghiệp Vụ
Trong các dự án di chuyển và đồng bộ dữ liệu doanh nghiệp (như di chuyển dữ liệu từ hệ thống Legacy sang SAP S/4HANA), thách thức lớn nhất là sự bất đồng bộ về cấu trúc dữ liệu giữa các bên:
- Hệ thống cũ lưu tên khách hàng trong 1 trường `NAME` duy nhất (255 ký tự).
- Hệ thống SAP S/4HANA chia thành `NAME1`, `NAME2`, `NAME3`, `NAME4` (mỗi trường tối đa 35 ký tự).

**`governance-schema-agent`** đóng vai trò là kiến trúc sư dữ liệu tự động:
- Đọc hiểu cấu trúc schema của cả hai phía (thông qua từ điển dữ liệu SAP Data Dictionary hoặc file DDL).
- Phân tích độ tương thích và cảnh báo nguy cơ cắt cụt dữ liệu (Data Truncation).
- Đề xuất chiến lược phân tách hoặc gộp trường dữ liệu tối ưu.

---

### 1.2. Các Tính Năng Nổi Bật:
1. **Phân Tích Tương Thích Tự Động (Schema Compatibility Analysis):**
   - So sánh hai schema và sinh báo cáo chênh lệch (Schema Diff): các trường mới, các trường bị xóa, sự thay đổi về độ dài ký tự và ràng buộc Nullable.
2. **Khuyến Nghị Chuẩn Hóa (Governance Recommendations):**
   - Tự động phát hiện các trường nhạy cảm (PII, dữ liệu tài chính) để gắn thẻ bảo mật và khuyến nghị chính sách mã hóa.

---

## 2. `validation-rule-agent` (Sinh & Kiểm Thử Luật Dữ Liệu)

### 2.1. Mục Tiêu Nghiệp Vụ
Trước khi dữ liệu được nạp vào SAP, nó phải vượt qua hàng trăm quy tắc kiểm tra tính hợp lệ (Data Quality Rules). Việc viết các biểu thức kiểm tra bằng tay vừa tốn thời gian vừa dễ bỏ sót lỗi.

**`validation-rule-agent`** chuyển đổi các yêu cầu chính sách kinh doanh thành các quy tắc kiểm tra tự động:
- Ví dụ người dùng yêu cầu: *"Mã số thuế doanh nghiệp Việt Nam phải có 10 hoặc 13 chữ số, ngày sinh nhân viên phải từ 18 tuổi trở lên, và email bắt buộc có đuôi công ty."*
- Agent tự động sinh ra các biểu thức Regex, hàm kiểm tra logic và mã lỗi tương ứng.

---

### 2.2. Các Loại Quy Tắc Được Sinh Tự Động:

| Loại Quy Tắc | Ví Dụ Kiểm Tra Thực Tế | Biểu Thức / Logic Được Sinh |
|---|---|---|
| **Định Dạng (Format)** | Kiểm tra định dạng Email, Số điện thoại, Mã số thuế. | Regex: `^[0-9]{10}(-[0-9]{3})?$` |
| **Phạm Vi (Range)** | Tuổi nhân viên từ 18 đến 65 tuổi. | `DATEDIFF(YEAR, DOB, TODAY) BETWEEN 18 AND 65` |
| **Phụ Thuộc (Dependency)** | Nếu Quốc gia là 'VN' thì Tỉnh/Thành phố bắt buộc phải có giá trị. | `IF Country == 'VN' THEN City IS NOT NULL` |
| **Tính Duy Nhất (Uniqueness)**| Mã khách hàng không được trùng lặp trong toàn bộ tập dữ liệu. | `COUNT(CustomerID) OVER (PARTITION BY CustomerID) == 1` |
| **Đối Chiếu (Referential)** | Mã tiền tệ phải nằm trong danh mục ISO chuẩn (VND, USD, EUR, JPY). | `Currency IN ('VND', 'USD', 'EUR', 'JPY')` |

---

## 3. Tích Hợp Vào Quy Trình AI Workflow

Các luật dữ liệu do `validation-rule-agent` sinh ra được xuất bản dưới dạng file JSON chuẩn hóa. Trong **AI Workflow Management**, node `TASK` sử dụng `mapping-data-worker` hoặc node `COMPUTE` có thể nạp trực tiếp tập luật này để quét sạch dữ liệu lỗi trước khi nạp vào hệ thống đích.
