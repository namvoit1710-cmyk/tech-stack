# 02. Định Tuyến Độ Khó Quy Tắc (Rule Difficulty Router)

> **Phân hệ:** Data Factory Platform  
> **Chủ đề:** Cơ chế phân loại độ khó quy tắc (Simple vs Medium vs Hard) và tối ưu hóa đường dẫn thực thi (Execution Path Optimization).

---

## 1. Vấn Đề Lãng Phí Tài Nguyên Trong Xử Lý Quy Tắc

Trong một tập gồm 126 quy tắc di chuyển dữ liệu:
- **80% quy tắc là rất đơn giản:** Kiểm tra trường bắt buộc (`required`), kiểm tra độ dài chuỗi (`length`), kiểm tra số dương (`range > 0`).
- **15% quy tắc ở mức trung bình:** Tra cứu mã danh mục trong bảng tham chiếu (`lookup`), so sánh 2 cột (`cross_field`).
- **5% quy tắc là cực kỳ phức tạp:** Đánh giá biểu thức logic đa tầng (`custom_expression`), so sánh mờ ngữ nghĩa trên tổ hợp nhiều cột (`composite_semantic_unique`).

Nếu coi mọi quy tắc đều như nhau và đưa toàn bộ qua một động cơ biểu thức nặng nề, tốc độ xử lý sẽ bị kéo chậm lại hàng chục lần!

Module **Rule Difficulty Router** phân loại và định tuyến từng quy tắc tới công cụ thực thi tối ưu nhất:

```mermaid
flowchart TD
    RULES_INPUT["Tập 126 Quy Tắc Đầu Vào"] --> ROUTER{"Bộ Định Tuyến Độ Khó (Difficulty Router)"}
    
    ROUTER -->|Mức Đơn Giản (Simple)| PATH_SQL["1. Đẩy Thẳng Xuống CSDL (SQL Pushdown / Polars SIMD)<br/>required · length · range · pattern"]
    ROUTER -->|Mức Trung Bình (Medium)| PATH_MEM["2. Xử Lý Bộ Nhớ Đệm (In-Memory Hash Lookup)<br/>reference_data · cross_field · in_list"]
    ROUTER -->|Mức Phức Tạp (Hard)| PATH_EXPR["3. Động Cơ Biểu Thức An Toàn (SafeExpression Engine)<br/>custom_expression · composite_semantic · multi_condition"]

    PATH_SQL --> RESULT["Hiệu Năng Tối Đa: Hơn 1,000,000 Dòng / Giây!"]
    PATH_MEM --> RESULT
    PATH_EXPR --> RESULT
```

---

## 2. Chi Tiết 3 Mức Phân Loại

### 2.1. Mức Đơn Giản (Simple / Tier 1)
- **Đặc điểm:** Chỉ đánh giá trên duy nhất một cột, không phụ thuộc vào dữ liệu bên ngoài.
- **Cơ chế thực thi:** 
  - Biên dịch trực tiếp thành các biểu thức cột song song của **Polars C-API**:
    ```python
    pl.col("MATNR").is_not_null() & (pl.col("MATNR").str.len_chars() <= 18)
    ```
  - Hoặc đẩy thẳng vào mệnh đề `WHERE` của câu lệnh truy vấn SAP HANA.
  - Tốc độ: **> 1,000,000 dòng / giây**, gần như không tốn CPU.

---

### 2.2. Mức Trung Bình (Medium / Tier 2)
- **Đặc điểm:** Cần so sánh giữa các cột hoặc tra cứu đối chiếu trong một danh mục tham chiếu cố định.
- **Cơ chế thực thi:** 
  - Nạp danh mục tham chiếu (ví dụ: Danh sách 200 mã quốc gia ISO) vào một bảng băm (**In-Memory HashSet**) trước khi quét mẻ.
  - Thực hiện phép kiểm tra tập hợp $O(1)$ cho mỗi dòng.

---

### 2.3. Mức Phức Tạp (Hard / Tier 3)
- **Đặc điểm:** Chứa các điều kiện phân nhánh lồng nhau (`IF ... ELSE`), tính toán số học động giữa nhiều cột, hoặc kiểm tra trùng lặp mờ.
- **Cơ chế thực thi:**
  - Định tuyến tới động cơ **`SafeExpression`** (sử dụng Abstract Syntax Tree - AST) để phân tích và đánh giá an toàn trong môi trường hộp cát.
  - Đảm bảo tính toán chính xác tuyệt đối mà không gây rủi ro bảo mật hệ thống.
