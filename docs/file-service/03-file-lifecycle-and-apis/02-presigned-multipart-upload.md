# 02. Tải Lên Nhiều Phần Kèm Presigned URLs (Multipart Upload)

> **Phân hệ:** File Service  
> **Chủ đề:** Tải lên tệp dung lượng lớn (GBs) qua Multipart Upload, ký trước URLs từng phần, và cơ chế ghép nối tệp tự động.

---

## 1. Khi Nào Cần Sử Dụng Multipart Upload?

Khi người dùng cần tải lên các tệp dữ liệu khổng lồ (từ 50MB đến hàng gigabytes như file sao lưu CSDL, tệp video, file nén zip lớn):
- Tải lên một lần (Single-part upload) rất dễ thất bại: Chỉ cần mạng chập chờn ở giây thứ 99, toàn bộ 99% dữ liệu trước đó đều bị mất và phải tải lại từ đầu!
- Gây nghẽn bộ nhớ RAM và băng thông của máy chủ API.

**Presigned Multipart Upload** giải quyết bài toán này:
- Chia tệp thành nhiều phần nhỏ (ví dụ: mỗi phần 10MB).
- Tải các phần lên **song song** (Parallel Uploads) trực tiếp vào AWS S3 / SeaweedFS.
- Nếu một phần bị lỗi mạng, client chỉ cần gửi lại duy nhất phần đó mà không phải tải lại toàn bộ tệp.

---

## 2. Vòng Đời 3 Bước Của Multipart Upload

```mermaid
sequenceDiagram
    autonumber
    actor Client as Trình Duyệt / Client
    participant API as File Service Multipart Controller
    participant S3 as AWS S3 / SeaweedFS Storage
    participant DB as SAP HANA Metadata DB

    rect rgb(240, 248, 255)
    Note over Client,API: Bước 1: Khởi Tạo Phiên (Initiate)
    Client->>API: POST /api/v1/multipart/initiate<br/>{ filename: "large_data.csv", size_bytes: 104857600, part_size: 10485760 }
    API->>S3: Gọi CreateMultipartUpload(bucket, key)
    S3-->>API: Trả về upload_id = "s3-up-9921"
    API->>S3: Ký trước danh sách Presigned PUT URLs cho từng Part (Part 1 -> 10)
    API->>DB: Tạo bản ghi phiên trong MULTIPART_UPLOAD_SESSIONS
    API-->>Client: 201 Created { upload_id, parts: [{ part_number: 1, presigned_url: "..." }, ...] }
    end

    rect rgb(255, 255, 240)
    Note over Client,S3: Bước 2: Tải Trực Tiếp Từng Phần Lên S3 (Client-to-Storage)
    par Tải song song Part 1, Part 2, ... Part 10
        Client->>S3: PUT Part 1 (presigned_url_1) -> S3 trả về ETag_1
    and
        Client->>S3: PUT Part 2 (presigned_url_2) -> S3 trả về ETag_2
    and
        Client->>S3: PUT Part 10 (presigned_url_10) -> S3 trả về ETag_10
    end
    end

    rect rgb(240, 255, 240)
    Note over Client,DB: Bước 3: Hoàn Tất & Hợp Nhất (Complete)
    Client->>API: POST /api/v1/multipart/complete<br/>{ upload_id, parts: [{ part_number: 1, etag: "ETag_1" }, ...] }
    API->>S3: Gọi CompleteMultipartUpload(upload_id, parts)
    S3-->>API: Hợp nhất tệp thành công!
    API->>DB: Cập nhật trạng thái session = COMPLETED, version = READY
    API-->>Client: 200 OK {"status": "SUCCESS", "file_id": "...", "version_id": "..."}
    end
```

---

## 3. Cơ Chế Hủy Bỏ An Toàn: `POST /multipart/abort`

Nếu người dùng bấm "Hủy tải lên" giữa chừng hoặc đóng trình duyệt:
- Client gọi: `POST /api/v1/multipart/abort` kèm `upload_id`.
- Hệ thống gửi lệnh `AbortMultipartUpload` sang S3 để xóa sạch toàn bộ các phần dữ liệu dở dang đã tải lên, giúp doanh nghiệp không bị tính phí lưu trữ cho các tệp rác.
- Có tiến trình dọn dẹp định kỳ tự động hủy các phiên upload multipart bị bỏ quên quá 24 giờ.
