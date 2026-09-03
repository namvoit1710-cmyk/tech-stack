# 03. Danh Mục Các Worker Tiêu Biểu (Workers Catalog)

> **Phân hệ:** AI Workflow Management  
> **Chủ đề:** Chi tiết tính năng và cấu hình của các worker dựng sẵn trong hệ thống.

---

## 1. `http-request-worker` (Gọi API / REST Client)

- **Vị trí mã nguồn:** `workflow/worker/http-request-worker/`
- **Mục đích:** Đóng vai trò là HTTP Client vạn năng, gọi các dịch vụ web bên ngoài hoặc dịch vụ nội bộ khác.
- **Phương thức hỗ trợ:** `GET`, `POST`, `PUT`, `DELETE`, `PATCH`.
- **Cấu hình chính:**
  - `url`: Địa chỉ API đích (hỗ trợ nội suy biến n8n-style `{{$node.data.url}}`).
  - `method`: Động từ HTTP.
  - `headers[]`: Danh sách key-value HTTP headers.
  - `query_params[]`: Tham số URL query string.
  - `body_type`: `JSON`, `Form-Data`, hoặc `Raw text`.
  - `authentication`: Hỗ trợ `None`, `Basic Auth`, `Bearer Token`, và `OAuth2 Client Credentials`.

---

## 2. `agent-worker` (Tác Vụ Trí Tuệ Nhân Tạo & LLMs)

- **Vị trí mã nguồn:** `workflow/worker/agent-worker/`
- **Mục đích:** Tích hợp với các mô hình ngôn ngữ lớn (Large Language Models) để thực hiện tóm tắt văn bản, trích xuất dữ liệu, hoặc lập kế hoạch đa bước.
- **Nhà cung cấp hỗ trợ:** OpenAI (GPT-4o, GPT-4 Turbo), Anthropic Claude (Claude 3.5 Sonnet).
- **Cấu hình chính:**
  - `provider`: `openai` hoặc `anthropic`.
  - `model`: Mã định danh model cụ thể.
  - `system_prompt`: Câu lệnh chỉ đạo bối cảnh và vai trò cho AI.
  - `user_prompt`: Nội dung prompt từ người dùng kèm biến nội suy.
  - `prompt_variables[]`: Danh sách biến gắn vào template prompt.
  - `conversation_history[]`: Ngữ cảnh hội thoại trước đó nếu là chat nhiều lượt.
  - `response_format`: `text` hoặc `json_object` (ép AI trả về JSON có cấu trúc).

---

## 3. `mapping-data-worker` (Biến Đổi & Ánh Xạ Dữ Liệu)

- **Vị trí mã nguồn:** `workflow/worker/mapping-data-worker/`
- **Mục đích:** Chuyển đổi cấu trúc JSON từ hệ thống này sang hệ thống khác mà không cần viết code thủ công.
- **Tính năng nổi bật:**
  - Hỗ trợ duyệt và ánh xạ các mảng lồng nhau (Nested arrays).
  - Trích xuất dữ liệu theo đường dẫn JSONPath: `$.orders[*].items[*].price`.
  - Cơ chế `strict_mode`: Báo lỗi nếu thiếu trường bắt buộc hoặc tự động bỏ qua nếu là trường tùy chọn.

---

## 4. `wait-worker` (Hẹn Giờ & Trì Hoãn)

- **Vị trí mã nguồn:** `workflow/worker/wait-worker/`
- **Mục đích:** Tạm dừng tiến trình theo thời gian thực trước khi chuyển tiếp sang node tiếp theo.
- **Chế độ hẹn giờ:**
  - `FIXED_DURATION`: Chờ một số giây/phút cố định (ví dụ chờ 30 giây để webhook đối tác kịp xử lý).
  - `UNTIL_DATETIME`: Tạm dừng cho đến một mốc ngày giờ cụ thể trong tương lai (ví dụ 08:00 AM sáng mai).
  - `MANUAL_APPROVAL`: Tạm dừng cho đến khi nhận được tín hiệu phê duyệt.

---

## 5. Các Worker Khác Trong Hệ Sinh Thái

- **`code-worker`:** Thực thi các đoạn script code Python/JavaScript tùy biến trong môi trường cô lập an toàn.
- **`database-connection-worker`:** Kết nối và thực thi các câu lệnh SQL trên SAP HANA, PostgreSQL, MySQL hoặc Oracle.
- **`jira-worker`:** Tự động tạo issue, cập nhật trạng thái ticket trên Atlassian Jira.
- **`email-worker` / `ms-teams-worker`:** Gửi thông báo tự động qua Email SMTP hoặc Microsoft Teams Webhook.
- **`req2tpl-worker`:** Phân tích yêu cầu tự nhiên của người dùng và chuyển đổi thành mẫu template cấu hình.
