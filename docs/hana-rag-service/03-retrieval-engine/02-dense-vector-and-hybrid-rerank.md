# 02. Tìm Kiếm Vector & Tái Xếp Hạng Lai (Dense Vector & Hybrid Rerank)

> **Phân hệ:** HANA RAG Service  
> **Chủ đề:** Tìm kiếm vector hai giai đoạn trên cột `REAL_VECTOR`, tái xếp hạng từ khóa `bm25s`, thuật toán RRF, và kỹ thuật HyDE.

---

## 1. Tìm Kiếm Vector Hai Giai Đoạn (Two-Stage Dense Retrieval)

Chỉ dựa vào khoảng cách Cosine của mô hình Embedding thôi là chưa đủ đối với dữ liệu doanh nghiệp:
- Vector Search rất giỏi tìm kiếm ý niệm tương đồng (Semantic similarity, ví dụ: "laptop" khớp với "máy tính xách tay").
- Nhưng Vector Search lại **rất kém** trong việc tìm kiếm từ khóa chính xác tuyệt đối (Exact Match: mã sản phẩm `X-9002-AB`, số hợp đồng `HD/2026/0491`, tên biến kỹ thuật).

Hệ thống kết hợp sức mạnh của cả hai thông qua **Advanced Hybrid Pipeline**:

```mermaid
flowchart TD
    QUERY["Câu Hỏi Của Người Dùng"] --> SPLIT{"Kích Hoạt Hybrid Pipeline"}
    
    subgraph STAGE_1["Giai Đoạn 1: Truy Thu Ứng Viên (Candidate Recall)"]
        SPLIT -->|Nhánh Ngữ Nghĩa (Dense)| HANA_VEC["SAP HANA COSINE_SIMILARITY<br/>(Lấy Top 50 Chunks tương đồng ngữ nghĩa)"]
        SPLIT -->|Nhánh Từ Khóa (Lexical)| BM25["Bộ Xếp Hạng bm25s<br/>(Lấy Top 50 Chunks khớp từ khóa chính xác)"]
    end

    subgraph STAGE_2["Giai Đoạn 2: Hòa Trộn & Tái Xếp Hạng (Fusion & Rerank)"]
        HANA_VEC --> RRF["Reciprocal Rank Fusion (RRF)<br/>Hòa trộn thứ hạng của 2 nguồn"]
        BM25 --> RRF
        RRF --> TOP_K["Chọn Lọc Top K Tinh Hoa (Ví dụ: Top 5 Bằng Chứng Tốt Nhất)"]
    end

    TOP_K --> CONTEXT["Đưa Vào Ngữ Cảnh Cho LLM"]
```

---

## 2. Thuật Toán Hòa Trộn Thứ Hạng: Reciprocal Rank Fusion (RRF)

Để kết hợp hai bảng xếp hạng có thang điểm khác nhau (điểm Cosine từ 0 đến 1, điểm BM25 từ 0 đến hàng trăm), hệ thống áp dụng công thức chuẩn RRF:

$$\text{RRF Score}(d) = \sum_{m \in M} \frac{1}{k + r_m(d)}$$

- Trong đó:
  - $M$: Tập hợp các phương pháp tìm kiếm (Dense Vector, BM25, GraphRAG).
  - $r_m(d)$: Thứ vị của tài liệu $d$ trong danh sách kết quả của phương pháp $m$ (1, 2, 3...).
  - $k$: Hằng số làm mượt chuẩn (mặc định $k = 60$).

**Ý nghĩa:** Một đoạn văn bản được xếp hạng cao ở cả hai phương pháp (vừa đúng ngữ cảnh vừa khớp chính xác từ khóa) sẽ có điểm RRF vượt trội và được đẩy lên hàng đầu.

---

## 3. Tăng Cường Truy Xuất: HyDE (Hypothetical Document Embeddings)

Khi người dùng đặt câu hỏi quá ngắn (ví dụ: *"Bảo hiểm y tế công tác nước ngoài"*):
- Câu hỏi chỉ có 7 từ, vector nhúng rất nghèo nàn thông tin.
- **Cơ chế HyDE:**
  1. Gọi một LLM nhỏ sinh ra một "đoạn tài liệu giả định" (Hypothetical Passage) mô phỏng câu trả lời có thể có trong tài liệu công ty.
  2. Dùng đoạn giả định giàu thông tin đó để tính vector embedding và đem đi so khớp với SAP HANA.
  3. Kết quả tìm kiếm thực tế đạt độ chuẩn xác cao hơn rõ rệt so với việc nhúng trực tiếp câu hỏi ngắn ngủi ban đầu.
