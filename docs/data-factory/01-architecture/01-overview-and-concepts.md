# 01. Tổng Quan & Các Khái Niệm Cốt Lõi (Overview & Core Concepts)

> **Phân hệ:** Data Factory Platform  
> **Chủ đề:** Mục đích kiến trúc, giải bài toán di chuyển dữ liệu lớn (Data Migration), động cơ chuyển đổi (Transformation) và bộ máy thẩm định chất lượng dữ liệu (Validation).

---

## 1. Bối Cảnh & Vấn Đề Di Chuyển Dữ Liệu Doanh Nghiệp

Trong quá trình chuyển đổi số và nâng cấp lên hệ thống SAP S/4HANA (hoặc sáp nhập dữ liệu Master Data từ nhiều hệ thống kế thừa Legacy):
- **Khối lượng dữ liệu khổng lồ:** Hàng triệu bản ghi vật tư (Material Master - `MARA`), đối tác kinh doanh (Business Partner), hóa đơn và hợp đồng.
- **Dữ liệu phân mảnh và sai chuẩn:** Thiếu các trường bắt buộc theo chuẩn SAP, sai định dạng ngày tháng, vi phạm ràng buộc miền giá trị, trùng lặp khóa chính.
- **Nguy cơ sập hệ thống (OOM Crashing):** Khi đọc và xử lý hàng triệu dòng dữ liệu cùng lúc, các worker truyền thống rất dễ bị tràn bộ nhớ (Out-Of-Memory) và bị Kubernetes hạ gục (`OOM-Killed`).

**Data Factory** (`apps/backend/data-factory`) ra đời như một **nhà máy xử lý dữ liệu tập trung (Data Processing Powerhouse)**, cung cấp khả năng:
1. **Di chuyển dữ liệu phi đồng bộ quy mô lớn (High-Throughput Data Migration)** từ các bảng ảo SAP HANA (HANA Virtual Tables).
2. **Bộ máy kiểm tra chất lượng dữ liệu (Data Validation)** với hơn 126 quy tắc sản xuất.
3. **Bộ máy biến đổi dữ liệu đa chiều (Row & Column Transformation)** với tốc độ xử lý hàng trăm ngàn dòng mỗi giây dựa trên thư viện **Polars**.
4. **Bộ điều chỉnh kích thước mẻ thích ứng (Adaptive Batching)** thông minh theo cgroups Linux container.

---

## 2. Mô Hình Vận Hành "HTTP As Trigger, Database As Delivery"

Điểm đột phá trong triết lý thiết kế của Data Factory là: **HTTP chỉ là cò súng kích hoạt (Trigger), còn CSDL là nơi nhận hàng (Delivery)**.

```mermaid
flowchart LR
    CALLER["Caller (Integration Hub)"] -->|1. POST /api/v1/data-migration/execute| DF["Data Factory Engine"]
    
    DF -->|2. Phản hồi 202 Accepted ngay tức thì kèm job_id| CALLER
    
    DF -->|3. Đọc dữ liệu thô hàng triệu dòng| VT[("HANA Virtual Table<br/>(STAGING_VT_MARA)")]
    
    DF -->|4. Áp dụng 126+ Luật Thẩm Định & Biến Đổi (Polars Engine)| DF
    
    DF -->|5. Xuất xưởng bảng vi phạm chi tiết| T_REP[("Bảng Báo Cáo:<br/>DF_REPORT_<job_id>")]
    DF -->|6. Xuất xưởng dữ liệu kết quả sạch| T_CB[("Bảng Dữ Liệu Sạch:<br/>DF_CB_<job_id>")]
    
    DF -.->|7. Bắn SSE Events cập nhật tiến độ %| CALLER
    DF -.->|8. Webhook Callback báo hoàn tất| CALLER
```

### Tại Sao Lại Chọn Thiết Kế Này?
- Một tiến trình chạy trên 500,000 dòng vật tư `MARA` mất từ 30 đến 90 giây. Nếu trả về kết quả qua thân phản hồi HTTP (Response Body), kết nối sẽ bị đứt do HTTP Timeout, đồng thời làm nghẽn băng thông mạng.
- Thay vào đó, Data Factory trả về ngay mã trạng thái **`202 Accepted`** cùng tên của 2 bảng đích:
  - **`DF_REPORT_<job_id>`:** Bảng lưu vết chi tiết từng dòng dữ liệu bị lỗi, mã quy tắc vi phạm và thông báo lỗi.
  - **`DF_CB_<job_id>`:** Bảng lưu toàn bộ dữ liệu hợp lệ đã được làm sạch và chuyển đổi, sẵn sàng để nạp thẳng vào bảng Master Data chính thức của SAP.

---

## 3. Các Định Dạng Tệp Hỗ Trợ Đa Dạng

Bên cạnh nguồn dữ liệu trực tiếp từ SAP HANA, Data Factory hỗ trợ nạp và xử lý qua 4 định dạng phổ biến:
- **`CSV`:** Tệp bảng tính phân cách dấu phẩy chuẩn.
- **`JSON`:** Dữ liệu có cấu trúc lồng nhau.
- **`XLSX` / `XLS`:** Bảng tính Microsoft Excel đa trang (Multi-sheet workbooks).
- **`Parquet`:** Định dạng lưu trữ dạng cột nén hiệu năng cao.
