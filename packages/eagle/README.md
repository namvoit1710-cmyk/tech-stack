# Eagle Platform Core (Smart Service SDK & Governance Smart API)

> **Mã nguồn lõi của Nền tảng AI Eagle — Động cơ phát hiện trùng lặp dữ liệu, so khớp thông minh, và phân tích tài liệu hóa chất SDS**

Thư mục này chứa toàn bộ mã nguồn lõi của phân hệ Eagle, bao gồm 2 dự án liên kết chặt chẽ:

---

## 📦 Các Dự Án Con

| Dự Án | Mô Tả | Đường Dẫn |
|---|---|---|
| **`smart-service-sdk`** | Bộ SDK lõi triển khai Clean Architecture 4 lớp cho AI Eagle. Cung cấp đường ống phát hiện trùng lặp lai (Exact + Fuzzy + Vector `REAL_VECTOR(640)` + Graph Evidence), động cơ tìm kiếm tương đồng ngữ nghĩa, phân tích tài liệu an toàn hóa chất **Material SDS**, và các tác vụ nạp chỉ mục chạy ngầm (Background Jobs). | [`./smart-service-sdk`](./smart-service-sdk/) |
| **`governance-smart-api`** | Cổng giao tiếp API hướng người dùng (Caller-facing API) cho quy trình nhập dữ liệu trùng lặp theo yêu cầu (Request-driven duplicate import). Tích hợp trực tiếp `smart_service_sdk` vào cùng tiến trình FastAPI, bổ sung router quản trị, nạp tệp CSV và hiển thị kết quả kiểm tra trực tiếp trên UI Console. | [`./governance-smart-api`](./governance-smart-api/) |

---

## 🚀 Khởi Chạy Nhanh

### 1. Cài Đặt Phụ Thuộc
```bash
cd packages/eagle/governance-smart-api
pip install -r requirements.txt
```
*(Lệnh trên sẽ tự động cài đặt `smart-service-sdk` ở chế độ editable `-e ../smart-service-sdk`)*

### 2. Khởi Chạy Dịch Vụ
```bash
python main.py
```
- API chạy tại: `http://127.0.0.1:8080` (hoặc cấu hình qua biến `PORT`)
- Swagger Docs: `http://127.0.0.1:8080/docs`
- Giao diện UI Console: `http://127.0.0.1:8080/ui`
