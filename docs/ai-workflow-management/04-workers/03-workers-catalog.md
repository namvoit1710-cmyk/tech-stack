# 03. Danh Mục Toàn Bộ 14 Worker Trong Hệ Sinh Thái (Workers Catalog)

> **Phân hệ:** AI Workflow Management  
> **Chủ đề:** Khảo sát chi tiết toàn bộ các worker chuyên biệt hóa trong thư mục `workflow/worker/`.

---

## 1. Bảng Tổng Quan Hệ Sinh Thái Worker

Hệ thống cung cấp một tập hợp đa dạng các worker chuyên biệt, mỗi worker được đóng gói thành một container độc lập:

| Tên Thư Mục Worker | Cổng Mạng Mặc Định | Chức Năng Chính | Nhà Cung Cấp / Công Nghệ Tích Hợp |
|---|---|---|---|
| `http-request-worker` | `36000` | Gọi RESTful HTTP APIs ngoài hệ thống. | `httpx`, Basic, Bearer, OAuth2 |
| `agent-worker` | `36001` | Thực thi các tác vụ LLM AI đa bước. | OpenAI API (GPT-4o, GPT-4 Turbo) |
| `claude-worker` | `36002` | Tác vụ AI chuyên sâu với Anthropic Claude. | Anthropic SDK (Claude 3.5 Sonnet) |
| `mapping-data-worker`| `36003` | Biến đổi cấu trúc JSON và mảng lồng nhau. | JSONPath, Custom Mapping Engine |
| `wait-worker` | `36004` | Hẹn giờ trì hoãn (Duration / Datetime). | Asyncio sleep, Durable Timers |
| `code-worker` | `36005` | Chạy code tùy biến Python/JS trong sandbox. | Python restricted execution |
| `database-connection-worker` | `36006` | Kết nối & chạy truy vấn CSDL quan hệ. | SAP HANA, PostgreSQL, MySQL |
| `gateway-worker` | `36007` | Cầu nối thực thi các Gateway App Functions. | OpenAPI Dynamic Proxy |
| `jira-worker` | `36008` | Tự động hóa Atlassian Jira (Create/Update). | Jira REST API v3 |
| `email-worker` | `36009` | Gửi email thông báo tự động. | SMTP / SendGrid / Microsoft 365 |
| `ms-teams-worker` | `36010` | Bắn tin nhắn thông báo vào kênh Microsoft Teams.| Teams Incoming Webhook Cards |
| `req2tpl-worker` | `36011` | Phân tích yêu cầu tự nhiên thành template. | LLM + Template Spec Engine |
| `log-worker` | `36012` | Ghi log kiểm toán tập trung (Audit Trail). | OpenTelemetry / ElasticSearch |
| `worker-sdk` | *(Thư viện)* | Bộ SDK nền tảng Clean Architecture. | Shared Core Library |

---

## 2. Chi Tiết Các Worker Tiêu Biểu

### 2.1. `claude-worker` & `agent-worker`
- **`claude-worker`:** Được tối ưu hóa cho mô hình Claude 3.5 Sonnet với cửa sổ ngữ cảnh lớn (200k tokens), rất mạnh trong việc phân tích mã nguồn, đọc hiểu tài liệu kỹ thuật phức tạp và sinh JSON chuẩn xác.
- **`agent-worker`:** Tích hợp OpenAI Function Calling, hỗ trợ chế độ System Prompt động và quản lý lịch sử hội thoại nhiều lượt.

### 2.2. `database-connection-worker`
- Cung cấp khả năng kết nối trực tiếp vào cơ sở dữ liệu quan hệ doanh nghiệp.
- Nhận cấu hình kết nối đã được mã hóa từ `run_credentials` (Host, Port, User, Password, SSL).
- Trả về dữ liệu dạng JSON mảng các bản ghi (records), tự động chuyển sang chế độ file-backed nếu số lượng bản ghi vượt ngưỡng 10,000 dòng.

### 2.3. `gateway-worker`
- Phối hợp cùng hệ thống **API Gateway**:
  - Tiếp nhận các tác vụ thuộc loại `GATEWAY_FUNCTION`.
  - Tự động nạp cấu hình xác thực (OAuth2 token / API Key) đã liên kết với Gateway App đó.
  - Định tuyến request tới máy chủ đích của đối tác và chuẩn hóa response trả về.

### 2.4. `req2tpl-worker` (Requirement to Template)
- Phân tích văn bản mô tả nghiệp vụ của người dùng thông qua mô hình AI để tự động sinh ra:
  - Cấu hình các node trong workflow.
  - Schema biểu mẫu auto-form phù hợp.
  - Gợi ý các kết nối dữ liệu giữa các bước.
