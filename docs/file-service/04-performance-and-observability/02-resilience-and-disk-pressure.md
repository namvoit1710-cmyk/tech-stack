# 02. Tính Bền Bỉ & Khả Năng Chống Chịu Lỗi (Resilience & Recovery)

> **Phân hệ:** File Service  
> **Chủ đề:** Cơ chế thử lại Exponential Backoff khi đồng bộ tệp, xử lý sự cố mạng S3, và chu trình tắt dịch vụ an toàn (Graceful Shutdown).

---

## 1. Khả Năng Chống Chịu Lỗi Đồng Bộ Đám Mây (S3 Sync Resilience)

Trong môi trường điện toán phân tán, mạng kết nối giữa máy chủ và AWS S3 hoặc cụm SeaweedFS có thể gặp sự cố bất cứ lúc nào (Mạng chập chờn, lỗi 503 Slow Down từ AWS, lỗi Timeout).

Nếu không có cơ chế xử lý bền bỉ:
- Tệp chỉ nằm trên đĩa cục bộ mà không bao giờ lên được đám mây.
- Khi máy chủ bị thay thế, dữ liệu sẽ biến mất vĩnh viễn!

`TieredStorageProvider` tích hợp thuật toán **Exponential Backoff kết hợp Jitter**:

```mermaid
flowchart TD
    SYNC_START["Bắt Đầu Tác Vụ Ngầm Đồng Bộ Lên S3"] --> ATTEMPT1["Lần 1: Đẩy Tệp Lên S3"]
    
    ATTEMPT1 -->|Thành Công| SUCCESS["Cập Nhật s3_synced_at = NOW ➔ Hoàn Tất!"]
    ATTEMPT1 -->|Thất Bại (Mạng Lỗi)| WAIT1["Chờ 1 giây (Backoff 1s)"]
    
    WAIT1 --> ATTEMPT2["Lần 2: Thử Lại Đẩy Tệp"]
    ATTEMPT2 -->|Thành Công| SUCCESS
    ATTEMPT2 -->|Thất Bại| WAIT2["Chờ 2 giây (Backoff 2s)"]
    
    WAIT2 --> ATTEMPT3["Lần 3: Thử Lại Đẩy Tệp"]
    ATTEMPT3 -->|Thành Công| SUCCESS
    ATTEMPT3 -->|Thất Bại Sau 3 Lần| RECORD_ERR["Ghi Nhận s3_sync_error Vào SAP HANA Metadata<br/>Giữ Nguyên Tệp An Toàn Trên Đĩa Cục Bộ!"]
    
    RECORD_ERR --> RECON["Tiến Trình Tái Đồng Bộ Định Kỳ (Reconciliation Cron) Sẽ Quét & Đẩy Bù Sau"]
```

---

## 2. Chu Trình Tắt Dịch Vụ An Toàn: Graceful Drain & Shutdown

Khi triển khai phiên bản mới trên Kubernetes (K8s Rolling Update) hoặc bảo trì hạ tầng:
- Máy chủ nhận tín hiệu dừng hệ thống (`SIGTERM`).
- **Nguyên tắc không bỏ rơi dữ liệu (No Data Abandonment):**
  1. File Service lập tức ngừng tiếp nhận các request upload mới (trả về 503 Service Unavailable).
  2. Dịch vụ cấp thời gian chờ (Grace Period, ví dụ 30 giây) để:
     - Toàn bộ các luồng Chunked Streaming đang tải dở về cho client hoàn tất.
     - Toàn bộ các background tasks đang đẩy tệp lên AWS S3 hoàn thành và cập nhật `s3_synced_at`.
  3. Đóng an toàn các kết nối CSDL SAP HANA connection pool trước khi tiến trình chính thoát hoàn toàn.
