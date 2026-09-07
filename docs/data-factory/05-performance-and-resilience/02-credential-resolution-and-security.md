# 02. Bảo Mật & Giải Quyết Chứng Thư Kết Nối (Credential Security)

> **Phân hệ:** Data Factory Platform  
> **Chủ đề:** Cơ chế bảo vệ thông tin đăng nhập SAP HANA qua mã thông báo đơn dụng `resolve-token` và bảo mật kết nối TLS.

---

## 1. Rủi Ro Lộ Lọt Mật Khẩu Khi Truyền Plaintext

Trong các môi trường doanh nghiệp chuẩn SOX, ISO 27001 và ngân hàng:
- Mọi kết nối tới CSDL SAP HANA chứa dữ liệu kinh doanh nhạy cảm (Material, Financial, Customer Data).
- Nếu API di chuyển dữ liệu yêu cầu gửi mật khẩu CSDL trực tiếp trong HTTP request body:
  - Mật khẩu sẽ bị ghi lại (Logged) trong các hệ thống giám sát mạng, Proxy logs, Ingress Gateway logs.
  - Bất kỳ lập trình viên nào có quyền xem log đều có thể thấy mật khẩu quản trị CSDL!

Data Factory giải quyết triệt để rủi ro này bằng mô hình **Cơ chế đổi mã thông báo đơn dụng (Single-Use Resolve-Token Scheme)**.

---

## 2. Quy Trình Giải Quyết Chứng Thư 3 Bước

```mermaid
sequenceDiagram
    autonumber
    participant IH as Integration Hub (Caller)
    participant DF as Data Factory (Engine)
    participant SECRET_SVC as Connection Secret Service
    participant HANA as SAP HANA Cloud Database

    rect rgb(240, 248, 255)
    Note over IH,DF: Bước 1: Gửi Token Ngắn Hạn (Không Chứa Mật Khẩu)
    IH->>DF: POST /api/v1/data-migration/execute<br/>{ credential: { scheme: "resolve-token", token: "UUID-TOKEN-9921", resolve_url: "..." } }
    end

    rect rgb(255, 255, 240)
    Note over DF,SECRET_SVC: Bước 2: Đổi Token Lấy Mật Khẩu Qua Kênh Nội Bộ
    DF->>SECRET_SVC: POST resolve_url { token: "UUID-TOKEN-9921" }
    SECRET_SVC->>SECRET_SVC: Xác thực token & HỦY BỎ NGAY LẬP TỨC (Single-Use Burn!)
    SECRET_SVC-->>DF: Trả về mật khẩu CSDL tạm thời
    end

    rect rgb(240, 255, 240)
    Note over DF,HANA: Bước 3: Mở Kết Nối Mã Hóa Tới SAP HANA
    DF->>HANA: Kết nối TLS Encrypted Connection (user, password, encrypt=true)
    HANA-->>DF: Kết nối thành công!
    DF->>DF: Xóa mật khẩu khỏi biến bộ nhớ RAM (Zeroization)
    end
```

---

## 3. Các Tiêu Chuẩn Bảo Mật Bổ Sung

1. **Hủy Token Ngay Sau Lần Dùng Đầu Tiên (Single-Use Token):**
   - Kể cả khi kẻ tấn công nghe lén được token trong gói tin HTTP, token đó đã hoàn toàn vô giá trị vì đã bị đốt (burned) ngay ở bước 2.
2. **Mã Hóa Đường Truyền CSDL Bắt Buộc (TLS Encryption):**
   - Kết nối tới SAP HANA luôn bật cờ `encrypt: true` đảm bảo toàn bộ hàng triệu dòng dữ liệu trung chuyển trên mạng đều được mã hóa bằng thuật toán TLS 1.3.
3. **Phòng Chống Thất Thoát Mã Thông Báo Khi Retry (Idempotency Protection):**
   - Khi request mang cùng một `job_id` được gửi lại (do mạng lag), Data Factory phát hiện `job_id` trùng lặp và không gọi lại `resolve_url`, ngăn chặn việc gây ra lỗi `403 Forbidden` do token đã hết hạn.
