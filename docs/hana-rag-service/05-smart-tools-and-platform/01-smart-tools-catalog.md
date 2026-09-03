# 01. Danh Mục Các Công Cụ Thông Minh (Smart Tools Catalog)

> **Phân hệ:** HANA RAG Service  
> **Chủ đề:** Khung nền tảng Smart Tools, vỏ bọc `ToolResult` chuẩn hóa, và danh mục các công cụ phân tích dữ liệu tích hợp sẵn.

---

## 1. Triết Lý Thiết Kế: "Mechanism, Not Policy"

Bên cạnh chức năng hỏi đáp tài liệu RAG, hệ thống cung cấp một phân hệ công cụ phân tích dữ liệu thông minh (**Smart Tools Platform**):
- **Cung cấp cơ chế (Mechanism):** Các công cụ cung cấp thuật toán xử lý dữ liệu mạnh mẽ, không phụ thuộc vào chính sách kinh doanh cụ thể nào.
- **Cấu hình dưới dạng dữ liệu (Config-as-Data):** Mỗi Tenant có thể định nghĩa các cấu hình độc lập (ngưỡng tương đồng, danh mục từ khóa, schema trích xuất) được lưu trong CSDL mà không cần sửa code.
- **Vỏ bọc kết quả chuẩn hóa (`ToolResult` Envelope):** Mọi công cụ khi chạy xong đều trả về cùng một cấu trúc hợp đồng dữ liệu:

```python
@dataclass
class ToolResult:
    success: bool
    data: Dict[str, Any]
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
```

---

## 2. Danh Mục Các Smart Tools Tích Hợp Sẵn

| Tên Công Cụ | Chức Năng Chính | Công Nghệ / Thuật Toán Sử Dụng |
|---|---|---|
| **`match/dedupe`** | Phát hiện các bản ghi trùng lặp trong tập dữ liệu Master Data (Khách hàng, Vật tư, Nhà cung cấp). | Thuật toán so khớp chuỗi mờ `RapidFuzz` kết hợp khoảng cách Vector Cosine trong SAP HANA. |
| **`keyword-screen`**| Rà soát và cảnh báo sự xuất hiện của các từ khóa cấm, rủi ro pháp lý, hoặc hóa chất độc hại trong tài liệu. | Aho-Corasick Automaton kết hợp regex matching hiệu năng cao. |
| **`extract`** | Trích xuất các trường dữ liệu cụ thể (Mã số thuế, Giá trị hợp đồng, Ngày hết hạn) từ văn bản tự do theo schema JSON định sẵn. | Structured LLM JSON Extraction với Pydantic Validation. |
| **`classify`** | Tự động gán nhãn và phân loại văn bản vào các danh mục nghiệp vụ (ví dụ: Hợp đồng mua sắm, Báo giá, Hóa đơn, Biên bản nghiệm thu). | Semantic Embedding Centroid Classifier hoặc Few-shot Prompting. |
| **`similarity`** | Đo lường độ tương đồng chính xác giữa hai đoạn văn bản hoặc hai hồ sơ thực thể nghiệp vụ. | Khoảng cách Cosine trên vector nhúng Harrier. |
| **`search`** | Công cụ tìm kiếm linh hoạt cho phép kết hợp cả bộ lọc metadata và từ khóa ngữ nghĩa. | Hybrid Search Engine với SQL pushdown. |

---

## 3. Điểm Nhấn: Công Cụ `match/dedupe` (Phát Hiện Bản Ghi Trùng Lặp)

Trong các dự án quản trị dữ liệu lớn, hàng ngàn bản ghi khách hàng bị tạo trùng lặp do lỗi gõ phím hoặc viết tắt:
- Bản ghi 1: *"Công ty TNHH Phần mềm ABC"*
- Bản ghi 2: *"Cty TNHH PM ABC Việt Nam"*

Công cụ **`match/dedupe`** áp dụng giải pháp hai tầng (Two-Tier Matching):
1. **Lọc nhanh (Candidate Screening):** Sử dụng các đặc trưng rút gọn (N-grams hoặc Semantic Clusters) để nhanh chóng chọn ra các cặp có nguy cơ trùng lặp.
2. **Chấm điểm chi tiết (Detailed Scoring):**
   - Điểm chuỗi ký tự (Token Sort Ratio, Levenshtein Distance) qua `RapidFuzz`.
   - Điểm ngữ nghĩa qua Cosine Similarity.
   - Trả về độ tương đồng tổng hợp và đề xuất hành động: *Gộp bản ghi (Merge)* hoặc *Giữ nguyên*.
