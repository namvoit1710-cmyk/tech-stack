# 02. Bảo Mật Doanh Nghiệp & Độ Bền Vững (Security & Resilience)

> **Phân hệ:** HANA RAG Service  
> **Chủ đề:** Cô lập đa người thuê (Tenant Isolation), phòng chống SSRF, Circuit Breakers cho dịch vụ tệp, và khung đo lường Observability.

---

## 1. Phòng Tuyến Bảo Mật Cấp Doanh Nghiệp

### 1.1. Cô Lập Đa Người Thuê Tuyệt Đối (No Tenant Bleed Policy)
Trong kiến trúc phần mềm SaaS cho doanh nghiệp, việc rò rỉ dữ liệu giữa các khách hàng là lỗi nghiêm trọng nhất.
- **Nguyên tắc "Fail-Closed" tại tầng dữ liệu:**
  - Mọi hàm truy vấn trong `HanaVectorRepository`, `HanaStructuredRepository`, và `HanaGraphRepository` đều bắt buộc phải nhận `tenant_id`.
  - Nếu thiếu `tenant_id`, câu lệnh sẽ ném lỗi `MissingTenantContextError` ngay tại tầng Application Use Case, không bao giờ được phép thực thi xuống database.
  - Mọi câu lệnh SQL đều chứa ràng buộc bắt buộc: `WHERE TENANT_ID = :tenant_id`.

---

### 1.2. Phòng Chống Tấn Công SSRF (Callback & URL SSRF Protection)
Hệ thống cho phép nạp tài liệu từ đường dẫn URL hoặc cấu hình Webhook thông báo khi Ingestion Job hoàn tất. Đây là mục tiêu ưa thích của các cuộc tấn công **Server-Side Request Forgery (SSRF)** nhằm thăm dò mạng nội bộ của doanh nghiệp.

Bộ kiểm tra an toàn mạng (`NetworkGuard`) chặn đứng:
- Các dải địa chỉ Loopback: `127.0.0.1`, `localhost`, `::1`.
- Các dải mạng nội bộ riêng (Private IPs): `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`.
- Địa chỉ IP Cloud Metadata: `169.254.169.254` (ngăn kẻ tấn công lấy cắp IAM Role Token của AWS/GCP/Azure).
- Chỉ cho phép gọi ra các URL thuộc danh sách trắng (**Host Allowlist**) hoặc các IP công khai đã được xác thực DNS an toàn.

---

## 2. Tính Bền Vững Của Kết Nối Tệp (File Service Resilience)

Khi đọc các tài liệu lớn từ kho lưu trữ từ xa (S3, Azure Blob, Remote File Service):
- **Cầu Chì Ngắt Mạch (Circuit Breaker):** Nếu dịch vụ lưu trữ tệp liên tục báo lỗi 5xx trong 5 lần liên tiếp, Circuit Breaker sẽ chuyển sang trạng thái `OPEN` trong 60 giây, lập tức từ chối các job nạp mới để không làm tắc nghẽn hàng đợi và bảo vệ hệ thống khỏi sụp đổ dây chuyền.
- **Giới Hạn Kích Thước Tệp (Byte Limits):** Từ chối ngay lập tức các tệp vượt quá ngưỡng cho phép (mặc định 100MB) trước khi tải về RAM để chống tấn công làm cạn kiệt tài nguyên (Zip Bomb / Denial of Service).
- **Cơ Chế Thử Lại Có Giãn Cách (Exponential Backoff):** Tự động thử lại tối đa 3 lần với thời gian chờ tăng dần khi gặp lỗi mạng tạm thời.

---

## 3. Khung Đánh Giá Chất Lượng Truy Xuất (Retrieval Evaluation Harness)

Làm thế nào để biết hệ thống RAG hoạt động có hiệu quả hay không?
Hệ thống tích hợp sẵn khung kiểm thử tự động **Evaluation Harness**:
- Sử dụng bộ dữ liệu chuẩn (Gold Evaluation Dataset) gồm hàng trăm câu hỏi có sẵn nhãn câu trả lời và tài liệu tham chiếu chính xác.
- Định kỳ đo lường các chỉ số chất lượng khoa học:
  - **Hit Rate @ K:** Tỷ lệ phần trăm câu hỏi mà tài liệu cần tìm xuất hiện trong Top K kết quả.
  - **MRR (Mean Reciprocal Rank):** Đánh giá vị trí xuất hiện của tài liệu đúng (nằm ở vị trí thứ 1 tốt hơn vị trí thứ 5).
  - **Context Precision & Recall:** Đo lường tỷ lệ thông tin nhiễu trong ngữ cảnh đưa vào cho LLM.

---

## 4. Giám Sát Đo Lường Tập Trung (Observability & Prometheus Metrics)

Hệ thống xuất bản các số liệu đo lường theo chuẩn OpenMetrics tại endpoint `GET /metrics`:
- `rag_retrieval_latency_seconds`: Phân bố thời gian phản hồi của bước truy xuất vector và SQL pushdown.
- `rag_ingestion_jobs_total`: Số lượng tác vụ nạp tài liệu theo trạng thái (`COMPLETED`, `FAILED`).
- `rag_vector_search_duration_seconds`: Thời gian thực thi phép toán `COSINE_SIMILARITY` trong SAP HANA.
- `rag_token_usage_total`: Tổng số lượng token tiêu thụ theo từng Tenant và từng mô hình AI.
