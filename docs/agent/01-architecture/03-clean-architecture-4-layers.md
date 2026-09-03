# 03. Kiến Trúc Phân Tầng Clean Architecture Trong Agent SDK

> **Phân hệ:** AI Agent Subsystem  
> **Chủ đề:** Chi tiết 4 tầng Clean Architecture áp dụng trong Agent SDK, cấu trúc module và quy chuẩn lập trình.

---

## 1. Nguyên Tắc Thiết Kế (The Dependency Rule)

Tương tự như `ai-workflow-management`, mọi Agent xây dựng trên **Agent SDK** đều phải tuân thủ nghiêm ngặt mô hình **Clean Architecture 4 tầng**:
- Tầng bên trong không bao giờ phụ thuộc vào tầng bên ngoài.
- Logic xử lý trạng thái và nghiệp vụ của Agent không được phụ thuộc vào giao thức truyền thông (HTTP/Kafka) hay nhà cung cấp LLM cụ thể.

```
┌─────────────────────────────────────────────────────────────┐
│  Layer 4: Frameworks & Drivers (FastAPI, HANA, LangGraph)   │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  Layer 3: Interface Adapters (REST Routes, Serializers)│ │
│  │  ┌──────────────────────────────────────────────────┐  │ │
│  │  │  Layer 2: Application (Nodes, Use Cases, Services)│ │
│  │  │  ┌────────────────────────────────────────────┐  │  │ │
│  │  │  │  Layer 1: Domain (State, TenantContext)    │  │  │ │
│  │  │  └────────────────────────────────────────────┘  │  │ │
│  │  └──────────────────────────────────────────────────┘  │ │
│  └────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. Chi Tiết 4 Tầng Trong Mã Nguồn Agent

| Tầng Kiến Trúc | Mã Nguồn Developer Viết | Thành Phần SDK Cung Cấp |
|---|---|---|
| **Layer 1: Domain Core** | `state.py`: Định nghĩa `MyAgentState` kế thừa từ `AgentBaseState` với các trường dữ liệu tùy biến. | `AgentBaseState`, `TenantContext`, `HitlInterruptPayload`, `AgentExecutionError`. |
| **Layer 2: Application** | `nodes.py`: Các hàm thuần túy thực thi bước suy luận của LangGraph: `(state, deps) -> partial_update`. | `ExecuteAgentUseCase`, `ResumeAgentUseCase`, `AgentDiscoveryService`, `EventScope`. |
| **Layer 3: Adapters** | Thường để trống hoặc bổ sung các DTO / Serializers tùy biến cho API. | FastAPI routes (`/api/v1/execute`, `/api/v1/resume`), Kafka consumer loops, response wrappers. |
| **Layer 4: Frameworks** | `build_graph()`: Kết nối các node thành đồ thị thông qua Builder của SDK. | `AgentGraphBuilder`, `ToolAgentBuilder`, `FlowGraphBuilder`, `OpenAIService`, `HanaCheckpointSaver`. |

---

## 3. Chi Tiết Từng Lớp

### 3.1. Layer 1: Domain Core (`layer1_domain/`)
- **`AgentBaseState`:** Lớp cơ sở đại diện cho bộ nhớ làm việc của Agent, tích hợp sẵn:
  - `messages`: Danh sách lịch sử tin nhắn hội thoại (hỗ trợ LangChain `BaseMessage`).
  - `tenant_context`: Định danh Tenant (`tenant_id`, `user_id`, `subdomain`) đảm bảo cô lập dữ liệu đa người thuê.
  - `iteration_count`: Đếm số vòng lặp suy luận, chống hiện tượng Agent chạy lặp vô tận.
- **`HitlInterruptPayload`:** Đối tượng chứa thông điệp ngắt khi Agent cần hỏi ý kiến con người.

---

### 3.2. Layer 2: Application Core (`layer2_application/`)
- **Các Node Xử Lý (`nodes.py`):**
  - Mỗi Node là một bước logic độc lập. Ví dụ: `analyze_request_node`, `generate_rules_node`, `format_response_node`.
  - Node chỉ nhận vào `state` hiện tại và các dependency được tiêm qua container (như `OpenAIService`, `Repository`), sau đó trả về dict chứa các trường cần cập nhật:
    ```python
    async def analyze_request_node(state: MyAgentState, deps: Dependencies) -> dict:
        result = await deps.llm.generate(state.user_prompt)
        return {"analysis_result": result}
    ```
- **Use Cases:**
  - `ExecuteAgentUseCase`: Nhận request, thiết lập session, kích hoạt đồ thị LangGraph và thu thập output.
  - `ResumeAgentUseCase`: Phục hồi phiên chạy từ checkpoint và tiếp tục luồng sau khi người dùng phản hồi.

---

### 3.3. Layer 3: Adapters (`layer3_adapters/`)
- Cung cấp sẵn các REST endpoints chuẩn cho mọi Agent:
  - `POST /api/v1/execute`: Kích hoạt lượt chạy mới.
  - `POST /api/v1/resume`: Cung cấp câu trả lời/phê duyệt của con người để tiếp tục session bị ngắt.
  - `GET /health` & `GET /ready`: Kiểm tra tình trạng hoạt động của container.
  - `GET /api/v1/info`: Xuất bản metadata và schema năng lực của Agent.

---

### 3.4. Layer 4: Frameworks & Drivers (`layer4_frameworks/`)
- Chứa toàn bộ các thư viện bên ngoài:
  - Khởi tạo LangGraph `StateGraph`.
  - Kết nối mô hình AI (OpenAI, Claude, Azure OpenAI) thông qua `LaidonLLM`.
  - Lưu trữ checkpoint trạng thái bền vững vào SAP HANA (`HanaCheckpointSaver`).
  - Lắng nghe và xuất bản thông điệp sự kiện qua Kafka hoặc SAP Event Mesh.
