# 02. Làm Sạch Dữ Liệu & Gợi Ý Luật So Khớp (Cleansing & Rule Suggestions)

> **Phân hệ:** AI Eagle Platform  
> **Chủ đề:** Làm sạch & làm giàu dữ liệu (`cleansing-enrichment`), và động cơ AI gợi ý luật quản trị dữ liệu (`rule-suggestions`).

---

## 1. Làm Sạch & Làm Giàu Dữ Liệu: `/api/v1/cleansing-enrichment/new-record`

Trước khi một bản ghi được đưa vào kho dữ liệu chính thức, nó cần được "tắm rửa" sạch sẽ:
- **Chuẩn Hóa Chuỗi (Standardization):**
  - Đưa tên riêng về dạng viết hoa chữ cái đầu tiêu chuẩn (`"CONG TY ABC"` ➔ `"Công ty ABC"`).
  - Chuẩn hóa số điện thoại theo định dạng quốc tế E.164 (`"0901234567"` ➔ `"+84901234567"`).
  - Chuẩn hóa định dạng địa chỉ hành chính (Tỉnh/Thành phố, Quận/Huyện, Phường/Xã) theo danh mục chuẩn quốc gia.
- **Làm Giàu Dữ Liệu (Enrichment):**
  - Dựa vào Mã số thuế, hệ thống tự động tra cứu và bổ sung thêm: Ngành nghề kinh doanh chính, Tình trạng doanh nghiệp đang hoạt động, Tên người đại diện pháp luật.

---

## 2. Động Cơ AI Gợi Ý Luật So Khớp: `/api/v1/rule-suggestions`

Một trong những khó khăn lớn nhất của các kỹ sư triển khai hệ thống quản trị dữ liệu là: **Làm sao để biết nên đặt luật so khớp nào cho từng trường dữ liệu?**
- Nếu đặt luật quá chặt ➔ Bỏ sót trùng lặp.
- Nếu đặt luật quá lỏng ➔ Báo động giả (False Positives) quá nhiều, làm ách tắc quy trình phê duyệt.

Endpoint `POST /api/v1/rule-suggestions` sử dụng AI để phân tích tập dữ liệu mẫu của doanh nghiệp:

```mermaid
flowchart TD
    SAMPLE["Tập Mẫu Dữ Liệu Doanh Nghiệp (1,000 Bản Ghi)"] --> AI_ANALYZE["AI Phân Tích Độ Độc Bản & Phân Bố Biến Thể"]
    
    AI_ANALYZE --> S1["TaxNumber có độ độc bản 99.9%<br/>Tỷ lệ sai chính tả cực thấp"]
    AI_ANALYZE --> S2["CompanyName có nhiều biến thể viết tắt<br/>Độ độc bản 85%"]
    AI_ANALYZE --> S3["StreetAddress có nhiều biến thể số nhà/hẻm"]

    S1 --> R1["Gợi Ý Luật 1: Exact Match trên TaxNumber (Trọng số: 0.40)"]
    S2 --> R2["Gợi Ý Luật 2: Fuzzy Match trên CompanyName (Threshold: 0.85, Trọng số: 0.35)"]
    S3 --> R3["Gợi Ý Luật 3: Fuzzy Match trên Street (Threshold: 0.75, Trọng số: 0.25)"]

    R1 & R2 & R3 --> PROPOSAL["Bộ Luật So Khớp Tối Ưu (Optimal Rule Set)<br/>Sẵn sàng áp dụng cho Production!"]
```

### Lợi Ích:
- Giúp doanh nghiệp thiết lập hệ thống Master Data Governance chuẩn chỉnh ngay từ ngày đầu tiên mà không cần thuê đội ngũ tư vấn dữ liệu đắt đỏ.
