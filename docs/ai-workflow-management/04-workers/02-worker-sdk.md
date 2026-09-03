# 02. Bộ Công Cụ Phát Triển Worker (Worker SDK)

> **Phân hệ:** AI Workflow Management  
> **Chủ đề:** Cấu trúc Clean Architecture của Worker SDK, vòng đời thực thi, và hướng dẫn xây dựng một worker mới.

---

## 1. Tổng Quan Về Worker SDK (`workflow/worker/worker-sdk`)

**Worker SDK** là một thư viện Python chuẩn hóa, cho phép các kỹ sư phần mềm xây dựng các microservice worker mới một cách nhanh chóng, đồng nhất và tuân thủ các quy chuẩn khắt khe của hệ thống:
- **Tự động hóa hoàn toàn hạ tầng:** Tự động đăng ký với Executor khi bật máy, tự động gửi nhịp tim định kỳ, tự động xử lý graceful shutdown.
- **Chuẩn hóa API & Metrics:** Tích hợp sẵn endpoint `/health`, `/metrics` (Prometheus) và chuẩn dữ liệu đầu vào/đầu ra.
- **Áp dụng Clean Architecture 4 lớp:** Đảm bảo mã nguồn xử lý nghiệp vụ của worker hoàn toàn tách biệt khỏi framework web.

---

## 2. Cấu Trúc Thư Mục Clean Architecture Của SDK

```
worker-sdk/worker_sdk/
├── layer1_domain/
│   ├── entities/         # TaskRequest, TaskResponse, WorkerInfo
│   └── value_objects/    # TaskStatus, WorkerStatus, NodeClass, Port
├── layer2_application/
│   ├── features/
│   │   └── execute_task/ # Use case thực thi tác vụ chính
│   └── interfaces/       # ITaskExecutor, ILogger, IMonitor, IStorage
├── layer3_adapters/
│   └── controllers/
│       ├── worker_server/   # FastAPI web server (cho chế độ SERVER)
│       └── worker_headless/ # Async loop runner (cho chế độ HEADLESS)
└── layer4_frameworks/
    ├── config/           # AppSettings (Pydantic settings)
    └── providers/        # HttpWorkerRegistry, PrometheusMonitor, LocalStorage
```

---

## 3. Hai Chế Độ Chạy Của Worker

| Đặc Điểm | Chế Độ SERVER | Chế Độ HEADLESS |
|---|---|---|
| **Cơ chế nhận việc** | Mở cổng HTTP, chờ Executor gọi vào `POST /api/v1/execute` | Chạy vòng lặp ngầm, chủ động kéo việc từ Executor (Pull model) |
| **Phù hợp với** | Các worker chạy trong Docker/K8s nội bộ mạng đám mây | Các worker chạy trên máy trạm cá nhân, phía sau firewall bảo mật |
| **Cổng mạng** | Cần mở cổng (ví dụ: 36000) | Không cần mở bất kỳ cổng inbound nào |

---

## 4. Hướng Dẫn Từng Bước Viết Một Worker Mới

### Bước 1: Kế thừa và hiện thực hóa `ITaskExecutor`
Bạn chỉ cần tạo một class và viết hàm xử lý nghiệp vụ chính:

```python
from worker_sdk.layer2_application.interfaces import ITaskExecutor
from worker_sdk.layer1_domain.entities import TaskRequest, TaskResponse
from worker_sdk.layer1_domain.value_objects import TaskStatus

class MyCustomExecutor(ITaskExecutor):
    async def execute(self, request: TaskRequest) -> TaskResponse:
        # 1. Đọc dữ liệu đầu vào
        user_id = request.input_data.get("user_id")
        
        # 2. Xử lý logic nghiệp vụ
        processed_data = {"status": "SUCCESS", "result": f"Processed {user_id}"}
        
        # 3. Trả về kết quả chuẩn hóa
        return TaskResponse(
            task_id=request.task_id,
            status=TaskStatus.COMPLETED,
            output_data=processed_data
        )
```

### Bước 2: Tạo điểm khởi chạy (`main.py`)

```python
from worker_sdk.runner import run_worker
from my_executor import MyCustomExecutor

if __name__ == "__main__":
    # run_worker sẽ tự động khởi động FastAPI, đăng ký với Executor và bắt đầu nhận việc
    run_worker(
        worker_type="my-custom-worker",
        executor_cls=MyCustomExecutor,
        default_port=36005
    )
```

Chỉ với khoảng 30 dòng mã, bạn đã có một microservice hoàn chỉnh sẵn sàng tham gia vào mạng lưới điều phối phân tán của AI Workflow Management!
