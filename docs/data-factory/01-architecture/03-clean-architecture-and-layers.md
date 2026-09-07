# 03. Kiến Trúc Phân Tầng Clean Architecture (Layers & Providers)

> **Phân hệ:** Data Factory Platform  
> **Chủ đề:** 4 tầng Clean Architecture, các Providers tối ưu bằng Polars, và nguyên lý độc lập nghiệp vụ.

---

## 1. Cấu Trúc 4 Tầng Trong Mã Nguồn

```
app/
├── layer1_domain/          # Thực thể nghiệp vụ thuần túy: Validation, Transformation, Schema
├── layer2_application/     # Use Cases: Data Migration, Row/Column Transform, Rule Catalog
├── layer3_adapters/        # REST Controllers v1, DTOs (RuleSpec), Serializers
└── layer4_frameworks/      # Polars Engine, SAP HANA Readers, Adaptive Batching, Storage
```

---

## 2. Chi Tiết Từng Tầng

### 2.1. Layer 1: Domain Core (`layer1_domain/`)
- Chứa các định nghĩa quy tắc trừu tượng và thực thể lõi:
  - `ValidationRule`: Định nghĩa luật thẩm định (Kiểu luật, trường áp dụng, tham số, thông báo lỗi).
  - `TransformationRule`: Định nghĩa phép biến đổi (Đổi tên, Regex, Lookup, Formula).
  - `SchemaTransformEntity`: Định nghĩa ánh xạ cấu trúc bảng từ nguồn sang đích.
  - `RuleManagement`: Quản lý danh mục luật dùng chung.

---

### 2.2. Layer 2: Application Core (`layer2_application/`)
Bao gồm các Use Cases tính năng:
- **`data_migration`:** Điều phối quy trình di chuyển dữ liệu lớn từ bảng ảo SAP HANA, phân mẻ mảng và ghi bảng kết quả.
- **`data_validation`:** Thực thi kiểm tra chất lượng dữ liệu trên mảng dòng hoặc toàn bộ bảng tính.
- **`data_transformation`:** Thực hiện biến đổi giá trị trường dữ liệu theo hàng hoặc theo cột.
- **`schema_transform`:** Thực hiện dịch chuyển cấu trúc bảng từ hệ thống nguồn sang SAP Target Schema.
- **`bundle`:** Đóng gói và thực thi chuỗi tuần tự nhiều bước biến đổi và kiểm tra trên nhiều sheet Excel cùng lúc.

---

### 2.3. Layer 3: Adapters (`layer3_adapters/`)
- Các REST Controllers tiếp nhận request HTTP:
  - `data_migration_controller.py`: Endpoint `/api/v1/data-migration/execute`, `/jobs/{id}`, `/jobs/{id}/events`.
  - `data_validation_controller.py`: Endpoint `/api/v1/data-validation/validate`.
  - `data_transformation_controller.py`: Endpoint `/api/v1/data-transformation/transform`.
  - `bundle_controller.py`: Endpoint `/api/v1/bundle/execute`.
  - `rule_catalog_controller.py`: Endpoint quản lý từ điển luật kiểm tra.

---

### 2.4. Layer 4: Frameworks & High-Performance Providers (`layer4_frameworks/`)
Đây là "trái tim công nghệ" mang lại tốc độ xử lý siêu việt cho Data Factory:
- **`PolarsValidatorProvider`:** Sử dụng thư viện [Polars](https://pola.rs/) (viết bằng ngôn ngữ Rust) để kiểm tra các luật dữ liệu song song trên đa nhân CPU.
- **`PolarsTransformerProvider` & `PolarsRowTransformerProvider`:** Bộ biến đổi dữ liệu cột và dòng cực nhanh, hỗ trợ cả LazyFrame và DataFrame.
- **`SafeExpression` (`providers/expressions/safe_expression.py`):** Động cơ đánh giá biểu thức tính toán an toàn trong môi trường hộp cát (Sandbox), ngăn ngừa hoàn toàn nguy cơ Remote Code Execution (RCE).
- **`AdaptiveBatching` (`providers/adaptive_batching.py`):** Tự động điều chỉnh kích thước mẻ theo dung lượng RAM của container Linux cgroups.
