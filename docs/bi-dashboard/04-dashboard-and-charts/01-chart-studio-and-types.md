# 01. Xưởng Thiết Kế Biểu Đồ (Chart Studio & Catalog)

> **Phân hệ:** BI Dashboard & Analytics Platform  
> **Chủ đề:** Cấu trúc thực thể `ChartDefinition`, danh mục 11 loại biểu đồ phân tích, và cơ chế cá nhân hóa người dùng.

---

## 1. Cấu Trúc Thực Thể `ChartDefinition`

Mỗi biểu đồ trong hệ thống được định nghĩa hoàn toàn dưới dạng dữ liệu (Chart-as-Data) thông qua thực thể `ChartDefinition` (`app/layer1_domain/entities/chart_definition.py`):

```json
{
  "chart_id": "chart-sales-by-region",
  "title": "Doanh Thu Theo Vùng Miền 2026",
  "chart_type": "BAR",
  "star_manifest_id": "manifest-sales-v1",
  "dimensions": [
    { "field": "DIM_CUSTOMER.REGION", "alias": "Vùng Miền", "sort": "ASC" }
  ],
  "measures": [
    { "field": "FACT_SALES.AMOUNT", "aggregation": "SUM", "alias": "Tổng Doanh Thu", "format": "CURRENCY_VND" }
  ],
  "filters": [
    { "field": "DIM_DATE.YEAR", "operator": "EQUALS", "value": 2026 }
  ],
  "color_palette": ["#6658DD", "#6366F1", "#F7B84B"],
  "refresh_interval_seconds": 300
}
```

---

## 2. Danh Mục 11 Loại Biểu Đồ Được Hỗ Trợ (Visualization Types)

Hệ thống cung cấp sẵn 11 loại biểu đồ doanh nghiệp dựng trên thư viện [Recharts](https://recharts.org/) và Tailwind CSS:

| Loại Biểu Đồ | Ký Hiệu Loại | Mục Đích Phân Tích Phù Hợp |
|---|---|---|
| **Cột (Bar / Column Chart)** | `BAR` | So sánh giá trị giữa các danh mục độc lập (Doanh thu theo vùng, Sản lượng theo nhà máy). |
| **Đường (Line Chart)** | `LINE` | Phân tích xu hướng biến động theo trục thời gian liên tục (Tăng trưởng doanh số qua 12 tháng). |
| **Vùng (Area Chart)** | `AREA` | Thể hiện mức độ tích lũy và đóng góp của các nhóm theo thời gian. |
| **Tròn & Bánh Donut** | `PIE` / `DONUT` | Trực quan hóa tỷ trọng cơ cấu phần trăm của một tổng thể (Thị phần sản phẩm). |
| **Thẻ Chỉ Số KPI (Metric Card)**| `KPI_CARD` | Hiển thị con số trọng yếu lớn (Doanh thu YTD, Tỷ lệ giao hàng đúng hạn) kèm % tăng giảm so với kỳ trước. |
| **Cây Phân Cấp (Treemap)** | `TREEMAP` | Trực quan hóa dữ liệu phân cấp nhiều tầng với diện tích khối tỷ lệ theo giá trị. |
| **Phễu Chuyển Đổi (Funnel)** | `FUNNEL` | Theo dõi tỷ lệ chuyển đổi qua các giai đoạn quy trình (Bán hàng, Tuyển dụng, Duyệt hồ sơ). |
| **Bản Đồ Nhiệt (Heatmap)** | `HEATMAP` | Nhận diện mật độ và điểm nóng tương tác theo hai chiều (Khung giờ x Ngày trong tuần). |
| **Phân Tán (Scatter Plot)** | `SCATTER` | Tìm mối tương quan giữa hai chỉ số định lượng khác nhau. |
| **Bảng Chi Tiết (Data Table)** | `TABLE` | Hiển thị bảng số liệu chi tiết hỗ trợ phân trang, sắp xếp và xuất dữ liệu Excel. |

---

## 3. Cá Nhân Hóa Người Dùng: `ChartPersonalization`

Một biểu đồ chuẩn của công ty có thể được nhiều nhân viên sử dụng theo cách riêng:
- Nhân viên A thích xem dạng biểu đồ Cột, nhân viên B thích xem dạng Bảng.
- Nhân viên A chỉ quan tâm tới khu vực Miền Bắc, nhân viên B chỉ quan tâm tới Miền Nam.

Module `chart_personalization` (`app/layer1_domain/entities/chart_personalization.py`):
- Cho phép mỗi người dùng lưu lại **Bộ lọc cá nhân (Personal Filters)**, thứ tự sắp xếp và chế độ xem ưa thích mà **không làm thay đổi** định nghĩa biểu đồ gốc của hệ thống.
- Cấu hình này được tự động nạp lại mỗi khi người dùng đăng nhập.
