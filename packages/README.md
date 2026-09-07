# SDKs & Developer Packages

> **Bộ công cụ phát triển phần mềm (Software Development Kits - SDKs) chính thức của SimpleMDG**

Thư mục này chứa mã nguồn hoàn chỉnh của các bộ SDK cốt lõi được sử dụng để xây dựng các Worker quy trình và các Tác nhân AI (Autonomous AI Agents) trong toàn bộ hệ sinh thái:

---

## 📦 Danh Mục Các Gói SDK

| Gói SDK | Mô Tả & Tính Năng Nổi Bật | Thư Mục |
|---|---|---|
| **Worker SDK (`worker-sdk`)** | Bộ SDK phát triển Worker thực thi quy trình cho **AI Workflow Management**. Hỗ trợ 3 chế độ chạy (`SERVER`, `PULL`, `HEADLESS`), đa giao thức (`gRPC` :50051 và `REST HTTP`), cơ chế giải quyết tham chiếu tệp qua **File Service**, bộ đệm kết quả `ResultBudget` và truyền phát dữ liệu lớn (Streaming I/O). | [`packages/worker-sdk`](./worker-sdk/) |
| **Agent SDK (`agent-sdk`)** | Bộ SDK xây dựng tác nhân AI tự trị trên nền tảng **LangGraph** cho **AI Agent Ecosystem**. Cung cấp 4 bộ dựng đồ thị (`ToolAgentBuilder`, `GraphAgentBuilder`, `FlowAgentBuilder`, `SubGraphAgentBuilder`), lưu vết trạng thái phân tán trên **SAP HANA** (`HanaCheckpointSaver`), tương tác người dùng phê duyệt **Human-in-the-loop (`HITL`)**, và quản lý ngân sách ngữ cảnh **Context Budget Manager** với 5 chiến lược nén. | [`packages/agent-sdk`](./agent-sdk/) |
| **Eagle Platform (`eagle`)** | Trọn bộ mã nguồn lõi của AI Eagle gồm `smart-service-sdk` (so khớp trùng lặp lai Exact/Fuzzy/Vector/Graph, phân tích hóa chất SDS) và `governance-smart-api` (cổng import dữ liệu ngầm và Web UI Console). | [`packages/eagle`](./eagle/) |
| **Data Factory (`data-factory`)** | Nhà máy xử lý và di chuyển dữ liệu lớn (Data Migration, Validation & Transformation Engine) tối ưu bằng nhân vector Polars, hỗ trợ 126+ quy tắc sản xuất, xuất xưởng bảng trực tiếp trên SAP HANA (`DF_REPORT_<id>` & `DF_CB_<id>`) và Adaptive Batching theo cgroups Linux container. | [`packages/data-factory`](./data-factory/) |

---

## 🚀 Hướng Dẫn Cài Đặt Nhanh

### 1. Worker SDK
```bash
cd packages/worker-sdk
pip install -e .
```
- Khởi chạy mẫu worker ở chế độ Server:
```python
from worker_sdk import WorkerApp, WorkerConfig

app = WorkerApp(config=WorkerConfig(name="custom-worker", mode="server", port=8000))

@app.task("transform_data")
async def handle_transform(payload):
    return {"status": "SUCCESS", "result": payload}

if __name__ == "__main__":
    app.run()
```

### 2. Agent SDK
```bash
cd packages/agent-sdk
pip install -e .
```
- Khởi tạo Agent với LangGraph và Checkpointing SAP HANA:
```python
from agent_sdk.builders import ToolAgentBuilder
from agent_sdk.checkpointers import HanaCheckpointSaver

builder = ToolAgentBuilder(name="governance-agent", model="gpt-4o")
# Cấu hình tools, state, và HANA Checkpointer
agent = builder.compile(checkpointer=HanaCheckpointSaver(...))
```
