# 01. Định Tuyến Truy Vấn & SQL Pushdown (Query Routing)

> **Phân hệ:** HANA RAG Service  
> **Chủ đề:** Bộ định tuyến truy vấn thông minh, phân biệt dữ liệu bảng biểu vs ngữ nghĩa, và cơ chế Structured SQL Pushdown.

---

## 1. Bài Toán Định Tuyến Truy Vấn (The Query Routing Challenge)

Người dùng cuối đặt câu hỏi bằng ngôn ngữ tự nhiên nhưng bản chất dữ liệu cần tìm lại thuộc hai thế giới hoàn toàn khác nhau:
1. **Câu hỏi ngữ nghĩa (Semantic Questions):**
   - *"Chính sách bảo hành của công ty quy định như thế nào về việc đổi trả sản phẩm bị lỗi?"*
   - Cần tìm kiếm trong các đoạn văn bản (Text Chunks) bằng so khớp vector.
2. **Câu hỏi dữ liệu có cấu trúc (Structured / Analytical Questions):**
   - *"Tổng chi phí mua sắm vật tư loại A trong tháng 5 năm 2026 là bao nhiêu?"*
   - *"Liệt kê top 5 nhà cung cấp có số lượng đơn hàng giao chậm nhiều nhất?"*
   - **Bắt buộc** phải dùng toán tử tập hợp (`SUM`, `COUNT`, `TOP-K`) trên bảng dữ liệu quan hệ, tìm kiếm vector hoàn toàn bất lực.

---

## 2. Kiến Trúc Bộ Định Tuyến Truy Vấn (`SemanticRouter`)

```mermaid
flowchart TD
    QUERY["Câu Hỏi Của Người Dùng (Natural Language Query)"] --> ROUTER["Bộ Định Tuyến Truy Vấn (Query Router)"]
    
    ROUTER --> CLASSIFY{"Phân Loại Ý Định Truy Vấn"}
    
    CLASSIFY -->|Ý Định Số Liệu / Bảng Biểu (Structured)| SQL_PATH["1. Nhánh Structured SQL Pushdown"]
    CLASSIFY -->|Ý Định Ngữ Nghĩa / Văn Bản (Unstructured)| HYBRID_PATH["2. Nhánh Hybrid Vector Retrieval"]
    CLASSIFY -->|Câu Hỏi Hỗn Hợp (Hybrid Query)| BOTH_PATH["3. Kích Hoạt Cả Hai & Hòa Trộn Kết Quả"]
    
    SQL_PATH --> EXEC_SQL["Sinh SQL Chuẩn Hóa & Thực Thi Trên RAG_STRUCTURED_ROWS"]
    HYBRID_PATH --> EXEC_VEC["Vector Cosine Similarity + bm25s Lexical Rerank"]
    BOTH_PATH --> MERGE["Tổng Hợp Bằng Chứng (Evidence Aggregator)"]
    
    EXEC_SQL --> SYNTHESIS["Gửi Bằng Chứng Tới LLM Sinh Câu Trả Lời"]
    EXEC_VEC --> SYNTHESIS
    MERGE --> SYNTHESIS
```

---

## 3. Cơ Chế Structured SQL Pushdown An Toàn

Khi nhánh **Structured SQL Pushdown** được kích hoạt:
1. **Trích Xuất Cấu Trúc & Bảng Mục Tiêu:**
   - Hệ thống xác định bảng tính liên quan dựa trên metadata và tên bảng trong Knowledge Base.
2. **Sinh SQL Pushdown Tham Số Hóa (Parameterized SQL Generation):**
   - Thay vì để LLM tự do viết câu SQL tùy ý có nguy cơ bị SQL Injection, hệ thống ánh xạ câu hỏi vào một khung cú pháp an toàn (Template Schema):
   ```sql
   SELECT 
       SUPPLIER_NAME, 
       SUM(AMOUNT) AS TOTAL_SPEND, 
       COUNT(ORDER_ID) AS ORDER_COUNT
   FROM RAG_STRUCTURED_ROWS
   WHERE TENANT_ID = :tenant_id
     AND DOCUMENT_ID = :target_doc_id
     AND SPEND_CATEGORY = :category
   GROUP BY SUPPLIER_NAME
   ORDER BY TOTAL_SPEND DESC
   LIMIT 5;
   ```
3. **Thực Thi Trực Tiếp Trong In-Memory HANA:**
   - Câu lệnh được đẩy xuống thực thi với tốc độ mili-giây trên động cơ tính toán trong bộ nhớ của SAP HANA.
4. **Đóng Gói Bằng Chứng (Evidence Enveloping):**
   - Dữ liệu kết quả được định dạng thành bảng JSON hoặc Markdown rõ ràng, làm cơ sở không thể chối cãi cho LLM trích dẫn trong câu trả lời cuối cùng.
