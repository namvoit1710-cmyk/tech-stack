# 01. Bóc Tách Đa Định Dạng Tài Liệu (Multi-Format Parsing)

> **Phân hệ:** HANA RAG Service  
> **Chủ đề:** Khả năng xử lý tài liệu đa định dạng, phát hiện ảnh quét tự động (Scan Detection) và cơ chế OCR Fallback.

---

## 1. Danh Sách Định Dạng Tệp Được Hỗ Trợ

Hệ thống RAG doanh nghiệp phải xử lý khối lượng lớn tài liệu phát sinh từ nhiều phòng ban với định dạng phong phú:

| Định Dạng Tệp | Trạng Thái | Thư Viện / Parser Sử Dụng | Chiến Lược Bóc Tách Nội Dung |
|---|---|---|---|
| **`.pdf`** | ✅ Hỗ trợ đầy đủ | `pdfplumber` + `Docling OCR` | Trích xuất văn bản số hóa; nếu phát hiện trang là ảnh quét sẽ tự động gọi Docling OCR. |
| **`.docx`** | ✅ Hỗ trợ đầy đủ | `Docling` / `python-docx` | Bóc tách phân cấp tiêu đề (Heading 1-6), bảng biểu và danh sách bullet. |
| **`.txt`** | ✅ Hỗ trợ đầy đủ | Built-in text parser | Đọc thô trực tiếp với cơ chế tự động nhận diện bảng mã (UTF-8, UTF-16, Latin). |
| **`.md`** | ✅ Hỗ trợ đầy đủ | Markdown Parser | Tận dụng cấu trúc Markdown (`#`, `##`, `###`) để phân đoạn ngữ nghĩa tự nhiên. |
| **`.json`** | ✅ Hỗ trợ đầy đủ | JSON Parser chuẩn hóa | Làm phẳng (Flatten) và chuẩn hóa cấu trúc thành văn bản có ngữ cảnh trước khi chunking. |
| **`.csv`** | ✅ Hỗ trợ đầy đủ | `Polars` / `csv` engine | **Không ép thành text chunks**; lưu trữ thành các hàng có cấu trúc (`RAG_STRUCTURED_ROWS`). |
| **`.xlsx`** | ✅ Hỗ trợ đầy đủ | `openpyxl` / `Calamine` | Đọc bảng tính đa sheet, xử lý header nhiều tầng, lưu dữ liệu bảng biểu quan hệ. |
| **`.doc` / `.xls`** | ❌ Từ chối an toàn | — | Bị từ chối tại cửa ngõ kèm thông báo hướng dẫn người dùng chuyển đổi sang định dạng XML hiện đại (`.docx`, `.xlsx`). |

---

## 2. Quy Trình Xử Lý PDF: Scan Detection & OCR Fallback

Tài liệu PDF trong doanh nghiệp thường chia làm hai loại: PDF sinh ra từ phần mềm (có lớp văn bản số) và PDF do scan máy quét (chỉ là tập hợp các bức ảnh).

Quy trình bóc tách thông minh của HANA RAG Service:

```mermaid
flowchart TD
    PDF["Tệp PDF Tải Lên"] --> P1["1. Đọc Thử Bằng pdfplumber"]
    P1 --> CHECK{"Mật Độ Ký Tự Số Hóa Trên Trang Có Đạt Ngưỡng? (Text Density)"}
    
    CHECK -->|Đạt Chuẩn (Văn Bản Số)| EXTRACT["Trích Xuất Trực Tiếp Lớp Text & Bảng Biểu"]
    CHECK -->|Không Đạt (Ảnh Quét / Scanned)| OCR["2. Kích Hoạt Docling OCR Fallback Engine"]
    
    OCR --> VISUAL["Nhận Diện Ký Tự Quang Học & Layout Phân Đoạn"]
    VISUAL --> EXTRACT
    
    EXTRACT --> CHUNK["Chuyển Sang Bước Chunking Ngữ Nghĩa"]
```

### Lợi Ích Của Chiến Lược Fallback:
- **Tiết Kiệm Tài Nguyên:** Không bật OCR một cách mù quáng cho toàn bộ tệp PDF (vì OCR ngốn CPU/GPU gấp 20 lần).
- **Không Bỏ Sót Nội Dung:** Với các hợp đồng đóng dấu đỏ hoặc hóa đơn scan, hệ thống tự động phát hiện và kích hoạt OCR cục bộ cho riêng những trang đó, đảm bảo 100% nội dung được số hóa.
