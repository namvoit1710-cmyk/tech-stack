# 03. Kiến Trúc Phân Tầng Clean Architecture (Layers & Interfaces)

> **Phân hệ:** File Service  
> **Chủ đề:** Chi tiết 4 tầng Clean Architecture, hệ thống Interfaces cốt lõi và các Use Cases nghiệp vụ.

---

## 1. Mô Hình Phân Tầng Nghiêm Ngặt

File Service tuân thủ chặt chẽ nguyên tắc Dependency Rule: Tầng ứng dụng và nghiệp vụ lõi hoàn toàn độc lập với các thư viện lưu trữ cụ thể (AWS SDK, SeaweedFS REST, SAP HANA Driver):

```
app/
├── layer1_domain/          # Thực thể tệp, siêu dữ liệu, ngoại lệ nghiệp vụ, interfaces lõi
├── layer2_application/     # Các Use Cases tính năng: upload, download, multipart, clone
├── layer3_adapters/        # REST Controllers v1, DTOs, Serializers
└── layer4_frameworks/      # S3/SeaweedFS Adapters, Tiered Storage, HANA Metadata Repo
```

---

## 2. Chi Tiết Từng Tầng Trong Mã Nguồn

### 2.1. Layer 1: Domain Core (`layer1_domain/`)
- **Entities & Value Objects:**
  - `FileRecord`: Thực thể tệp logic (`file_id`, `tenant_id`, `filename`, `current_version_id`).
  - `FileVersionRecord`: Chi tiết một phiên bản (`version_id`, `size_bytes`, `checksum_sha256`, `raw_storage_key`, `processed_storage_key`).
  - `MultipartUploadSession`: Phiên tải lên nhiều phần cho tệp lớn (`upload_id`, `parts_count`).
- **Interfaces:**
  - `IStorageProvider`: Giao diện trừu tượng hóa toàn bộ thao tác lưu trữ (`upload_stream`, `download_stream`, `delete_object`, `generate_presigned_url`).
  - `IFileMetadataRepository`: Giao diện lưu trữ siêu dữ liệu tệp (`save_file`, `get_file`, `create_version`, `update_version_status`).

---

### 2.2. Layer 2: Application Core (`layer2_application/`)
Chứa toàn bộ các Use Case nghiệp vụ độc lập:
- **`upload_file`:** Tiếp nhận luồng tải lên, kiểm tra kích thước, điều phối chuẩn hóa CSV và tạo bản ghi phiên bản mới.
- **`download_file`:** Xác định phiên bản cần tải (mới nhất hoặc theo `version_id`), quyết định phục vụ từ cache cục bộ hay kéo từ Cloud S3.
- **`presigned_multipart_upload`:** Khởi tạo phiên upload nhiều phần, ký trước URL cho từng Part, và gọi hoàn tất (Complete Multipart) trên S3.
- **`clone_file`:** Sao chép tệp trong kho lưu trữ với chi phí tối thiểu mà không cần tải dữ liệu về máy khách.
- **`query_file_data`:** Đọc và lọc nhanh dữ liệu trong tệp bảng tính theo dòng (Streaming Slice).

---

### 2.3. Layer 3: Adapters (`layer3_adapters/`)
- **REST Controllers (`layer3_adapters/controllers/restful/v1/`):**
  - `file_controller.py`: Endpoints tải lên, tải xuống, lấy metadata, xóa tệp.
  - `multipart_controller.py`: Endpoints quản lý vòng đời Multipart Upload.
  - `performance_controller.py`: Endpoints truy vấn dữ liệu giám sát hiệu năng CPU/RAM.
  - `batch_controller.py`: Endpoints xử lý thao tác hàng loạt (Batch operations).

---

### 2.4. Layer 4: Frameworks & Drivers (`layer4_frameworks/`)
Hiện thực hóa kỹ thuật các giao diện của tầng 1:
- **Kho Lưu Trữ Đối Tượng (Storages):**
  - `TieredStorageProvider`: Bộ điều phối lưu trữ phân tầng đệm cục bộ SSD + Cloud S3.
  - `S3StorageProvider`: Kết nối AWS S3 / MinIO bằng thư viện `boto3` / `aioboto3`.
  - `SeaweedFSStorageProvider`: Kết nối cụm lưu trữ SeaweedFS qua Filer REST API.
- **Kho Siêu Dữ Liệu (Metadata):**
  - `HanaFileMetadataRepository`: Hiện thực hóa trên CSDL SAP HANA Cloud bằng `hdbcli`.
  - `SqliteFileMetadataRepository`: Hiện thực hóa trên SQLite cục bộ cho lập trình viên phát triển nhanh.
