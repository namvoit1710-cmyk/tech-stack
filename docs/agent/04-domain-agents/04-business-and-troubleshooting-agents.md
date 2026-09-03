# 04. Tác Nhân Chẩn Đoán Lỗi, Nghiệp Vụ & LaidonLLM Gateway

> **Phân hệ:** AI Agent Subsystem  
> **Chủ đề:** Tác nhân chẩn đoán sự cố `troubleshooting-agent`, tác nhân kinh doanh tùy biến `business-agent`, và tầng kết nối mô hình `laidonllm`.

---

## 1. `troubleshooting-agent` (Chẩn Đoán Sự Cố & Phân Tích Nguyên Nhân Gốc Rễ)

### 1.1. Mục Tiêu Nghiệp Vụ
Khi một Workflow phức tạp gồm 30 node bị dừng đột ngột ở trạng thái `FAILED`, người dùng thường bối rối trước các thông báo lỗi kỹ thuật khó hiểu (ví dụ: `Errno 111 Connection refused`, `HDB-0102 SQL Unique constraint violation`, hoặc `KeyError: 'customer_id'`).

**`troubleshooting-agent`** đóng vai trò là kỹ sư hỗ trợ AI (AI Support Engineer):
- Được kích hoạt tự động hoặc thủ công khi một Run gặp sự cố.
- Tự động thu thập toàn bộ nhật ký lỗi (Traceback), log của từng node, và trạng thái ngữ cảnh biến tại thời điểm sụp đổ.

---

### 1.2. Quy Trình Phân Tích Nguyên Nhân Gốc Rễ (RCA Workflow):
1. **Thu Thập Chứng Cứ (Evidence Collection):** Đọc toàn bộ các sự kiện `NodeFailed`, `TaskFailed` từ Event Log của Run đó.
2. **Khảo Sát Ngữ Cảnh Dữ Liệu:** Kiểm tra dữ liệu đầu vào của node bị lỗi xem có trường nào bị `None` hoặc sai kiểu dữ liệu không.
3. **Đối Chiếu Cơ Sở Tri Thức (Knowledge Base Matching):** So khớp lỗi với danh mục các sự cố thường gặp (mạng timeout, sai credentials, lỗi schema SAP).
4. **Đề Xuất Bản Vá (Actionable Resolution):**
   - Giải thích nguyên nhân bằng ngôn ngữ tự nhiên dễ hiểu.
   - Hướng dẫn cụ thể cách khắc phục: *"Node số 5 bị lỗi vì mã số thuế gửi sang SAP thiếu 3 số cuối. Hãy sửa cấu hình node số 4 để format lại chuỗi."*
   - Cung cấp nút bấm kích hoạt **Rerun Task** hoặc **Rerun Run** ngay khi sửa xong.

---

## 2. `business-agent` & `business-agent-builder` (Tác Nhân Kinh Doanh Tùy Biến)

- **`business-agent`:** Không bị gắn chặt vào một nghiệp vụ kỹ thuật cố định. Nó là một tác nhân khung (Generic Business Agent) có thể được cấu hình linh hoạt theo từng phòng ban:
  - *Agent Tài Chính:* Đối soát công nợ khách hàng, kiểm tra hạn mức tín dụng.
  - *Agent Chuỗi Cung Ứng:* Dự báo tồn kho, kiểm tra thời gian giao hàng của nhà cung cấp.
- **`business-agent-builder`:**
  - Cung cấp giao diện trực quan cho phép các Business Analyst tạo ra Agent mới mà không cần lập trình:
  - Tải lên tài liệu quy trình nội bộ (SOPs).
  - Chọn các Tool API sẵn có từ Node Palette.
  - Thiết lập hướng dẫn System Prompt và quyền hạn truy cập.

---

## 3. Cổng Kết Nối Mô Hình AI: `laidonllm`

Nằm tại `apps/backend/agent/laidonllm/`, đây là tầng trừu tượng hóa toàn bộ việc gọi các mô hình AI trong hệ thống:
- **Hỗ trợ đa nhà cung cấp (Multi-Provider Abstraction):** Cho phép hoán đổi mượt mà giữa OpenAI, Azure OpenAI, Anthropic Claude, và các mô hình mã nguồn mở nội bộ (Llama 3, Qwen) mà không cần sửa code Agent.
- **Quản lý Token & Rate-Limiting:** Tự động theo dõi số lượng token tiêu thụ theo từng Tenant để tính chi phí (Token Billing & Quota).
- **Cơ Chế Tự Động Thử Lại (Resilient Fallback):** Nếu OpenAI báo lỗi `429 Too Many Requests`, gateway tự động chuyển hướng request sang Azure OpenAI hoặc Anthropic Claude để đảm bảo dịch vụ không bị gián đoạn.
- **Hỗ Trợ Streaming (Server-Sent Events):** Tối ưu hóa việc truyền phát token theo thời gian thực về giao diện Chat.
