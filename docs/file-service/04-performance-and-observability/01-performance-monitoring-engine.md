# 01. Động Cơ Giám Sát Hiệu Năng (Performance Monitoring)

> **Phân hệ:** File Service  
> **Chủ đề:** Hệ thống đo lường hiệu năng chi tiết (Execution Time, RAM Peak, CPU), lưu trữ nhật ký xoay vòng hàng ngày JSONL, và các API truy vấn.

---

## 1. Tầm Quan Trọng Của Việc Đo Lường Hiệu Năng Tệp

Dịch vụ tệp là thành phần có nguy cơ cao nhất về việc tiêu tốn tài nguyên hệ thống (I/O đĩa, băng thông mạng, và bộ nhớ RAM). Một đoạn mã xử lý tệp không tối ưu có thể làm tăng vọt RAM máy chủ hoặc làm CPU bị nghẽn (CPU Spike).

File Service tích hợp một hệ thống giám sát hiệu năng chuyên sâu đo lường 3 chỉ số vàng:
1. **Thời Gian Thực Thi (Execution Time):** Thời gian tính bằng giây tới độ chính xác micro-giây.
2. **Bộ Nhớ RAM (Memory Footprint):** Đo lường `memory_start`, `memory_end`, và đặc biệt là **`memory_peak`** (đỉnh RAM tiêu thụ trong suốt quá trình xử lý).
3. **Mức Độ Tiêu Thụ CPU:** Đo lường `cpu_start`, `cpu_end`, `cpu_average`, và `cpu_peak`.

---

## 2. Chiến Lược Lưu Trữ: Nhật Ký Xoay Vòng Hàng Ngày (Daily Rotating JSONL)

Thay vì ghi toàn bộ dữ liệu vào một tệp log khổng lồ hoặc bắt buộc phải dựng một cụm CSDL giám sát nặng nề (như Elasticsearch), File Service sử dụng định dạng **JSON Lines (.jsonl)** phân tách theo ngày:

```
performance_logs/
├── performance_2026-09-01.jsonl
├── performance_2026-09-02.jsonl
├── performance_2026-09-03.jsonl
└── ...
```

### Tại Sao Lại Dùng JSONL Xoay Vòng Hàng Ngày?
- **Tốc độ đọc/ghi cực nhanh:** Ghi nối tiếp (Append-only) vào cuối tệp mà không xảy ra tranh chấp khóa (Lock contention).
- **Dọn dẹp dễ dàng (Zero-Overhead Retention):** Tự động xóa các tệp quá hạn (mặc định lưu giữ **30 ngày** - `PERFORMANCE_MAX_FILES=30`) chỉ bằng một lệnh xóa tệp đơn giản.
- **Lọc theo khoảng ngày tức thì:** Khi truy vấn dữ liệu từ ngày A đến ngày B, hệ thống chỉ cần mở đúng các tệp của những ngày đó thay vì phải quét toàn bộ lịch sử.

---

## 3. Các API Truy Vấn Dữ Liệu Hiệu Năng

Hệ thống cung cấp 2 endpoints chuyên dụng phục vụ việc theo dõi và tích hợp vào Grafana/Dashboard:

### 3.1. Lấy Dữ Liệu Chi Tiết: `GET /api/v1/performance/data`
- **Bộ lọc hỗ trợ:** `start_date`, `end_date`, `function` (tên hàm), `module` (tên controller), `limit`.
- **Dữ liệu trả về:**

```json
{
  "data": [
    {
      "timestamp": "2026-09-03T14:30:45.123456",
      "function": "upload_file",
      "module": "file_controller",
      "execution_time": 0.456,
      "memory_start": 120.5,
      "memory_end": 121.2,
      "memory_peak": 128.8,
      "cpu_start": 12.0,
      "cpu_end": 15.2,
      "cpu_average": 13.8,
      "cpu_peak": 18.5
    }
  ],
  "count": 1
}
```

---

### 3.2. Lấy Báo Cáo Thống Kê Tổng Hợp: `GET /api/v1/performance/summary`
- Tự động tính toán các chỉ số thống kê cao cấp: **Min, Max, Trung bình (Average), Percentile P95, Percentile P99** cho từng hàm.
- Giúp đội ngũ vận hành nhận diện ngay lập tức các hàm có độ trễ cao hoặc tốn nhiều RAM bất thường để kịp thời tối ưu hóa.
