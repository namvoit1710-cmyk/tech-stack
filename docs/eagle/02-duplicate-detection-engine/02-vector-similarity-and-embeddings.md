# 02. Tương Đồng Vector & Nhúng Ngôn Ngữ (Vector Similarity)

> **Phân hệ:** AI Eagle Platform  
> **Chủ đề:** Tích hợp cột `REAL_VECTOR(640)` trong SAP HANA, mô hình nhúng cục bộ FastEmbed, và tối ưu hóa tính toán Cosine Similarity in-memory.

---

## 1. Lưu Trữ Vector Trong SAP HANA: `REAL_VECTOR(640)`

Khác với các hệ thống AI truyền thống phải sử dụng một CSDL Vector chuyên dụng tách rời (như Pinecone, Milvus, Qdrant) gây phân mảnh dữ liệu, AI Eagle tận dụng tính năng bản địa **Vector Engine** của SAP HANA Cloud:

```sql
CREATE COLUMN TABLE "AE_RAG_CHUNKS" (
    "CHUNK_ID" NVARCHAR(128) NOT NULL,
    "TENANT_ID" NVARCHAR(64) NOT NULL,
    "DOCUMENT_ID" NVARCHAR(64) NOT NULL,
    "CONTENT" NCLOB NOT NULL,
    "EMBEDDING" REAL_VECTOR(640) NOT NULL,
    PRIMARY KEY ("CHUNK_ID", "TENANT_ID")
);
```

### Lợi Thế Tuyệt Đối Của SAP HANA Vector Engine:
- **Zero Data Movement:** Vector được lưu ngay bên cạnh dữ liệu nghiệp vụ quan hệ (`TENANT_ID`, `DOCUMENT_ID`, `STATUS`).
- **Giao Dịch ACID & Đa Người Thuê (Multi-Tenancy):** Khi xóa hoặc sửa một bản ghi, vector của nó được cập nhật hoặc xóa tức thì trong cùng một transaction.
- **Tính Toán Song Song In-Memory:** Tận dụng tập lệnh SIMD (Single Instruction, Multiple Data) của chip CPU máy chủ để tính toán khoảng cách Cosine giữa hàng triệu vector trong vài mili-giây.

---

## 2. Mô Hình Nhúng Cục Bộ: FastEmbed (`bge-small-en-v1.5`)

Thay vì phụ thuộc vào việc gọi API nhúng của OpenAI ra ngoài Internet (gây tốn kém chi phí theo token và rủi ro bảo mật dữ liệu nhạy cảm của doanh nghiệp):
- AI Eagle sử dụng nhà cung cấp **`LocalEmbeddingProvider`** (`app/layer4_frameworks/embeddings/local_embedding_provider.py`).
- **Thư viện nền tảng:** [FastEmbed](https://github.com/qdrant/fastembed) kết hợp mô hình tối ưu `bge-small-en-v1.5`.
- **Ưu điểm:**
  - Chạy thuần túy trên CPU máy chủ với lượng tiêu thụ RAM cực nhỏ (< 500MB).
  - Tốc độ sinh vector nhúng lên tới **hơn 1,000 đoạn văn bản / giây**.
  - Hoàn toàn Offline: Dữ liệu khách hàng không bao giờ rời khỏi hạ tầng nội bộ của doanh nghiệp.

---

## 3. Câu Lệnh Truy Vấn Tương Đồng Cosine Đẩy Xuống CSDL (Pushdown Query)

```sql
SELECT 
    "CHUNK_ID",
    "DOCUMENT_ID",
    "CONTENT",
    COSINE_SIMILARITY("EMBEDDING", TO_REAL_VECTOR(:query_vector)) AS "SCORE"
FROM "AE_RAG_CHUNKS"
WHERE "TENANT_ID" = :tenant_id
  AND COSINE_SIMILARITY("EMBEDDING", TO_REAL_VECTOR(:query_vector)) >= :min_score
ORDER BY "SCORE" DESC
LIMIT :top_k;
```

Các tham số `:min_score` (mặc định `0.45`) và `:top_k` (mặc định `10`) được nạp động từ bảng cấu hình runtime `AE_SERVICE_CONFIGURATIONS`, cho phép tinh chỉnh độ nhạy của hệ thống theo từng tenant mà không cần khởi động lại dịch vụ.
