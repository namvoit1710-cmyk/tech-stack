# 01. Các Bộ Dựng Đồ Thị (Graph Builders)

> **Phân hệ:** AI Agent Subsystem  
> **Chủ đề:** 4 phương thức xây dựng đồ thị LangGraph trong Agent SDK: `ToolAgentBuilder`, `AgentGraphBuilder`, `FlowGraphBuilder`, và `SubGraphAgentBuilder`.

---

## 1. Tổng Quan Về Các Builder Paths

Để phù hợp với các mức độ phức tạp khác nhau của bài toán, Agent SDK cung cấp **4 phương thức dựng đồ thị (Builder Paths)**:

| Builder | Mức Độ Phức Tạp | Khi Nào Nên Dùng? |
|---|---|---|
| **`ToolAgentBuilder`** | Thấp (5 phút) | Agent chỉ cần nhận câu hỏi, tự chọn gọi một vài công cụ (Tools) rồi trả lời kết quả (ReAct Pattern chuẩn). |
| **`AgentGraphBuilder`** | Trung bình - Cao | Cần đồ thị tùy biến hoàn toàn: có các bước tiền xử lý (Pre-nodes), hậu xử lý (Post-nodes), phân nhánh phức tạp. |
| **`FlowGraphBuilder`** | Doanh nghiệp | Đồ thị được định nghĩa bằng cấu hình Declarative (JSON/YAML) lưu trong cơ sở dữ liệu hoặc Registry. |
| **`SubGraphAgentBuilder`** | Rất cao | Xây dựng hệ thống phân cấp: nhúng nguyên một Agent độc lập làm một bước con (Subgraph) trong Agent cha. |

---

## 2. Chi Tiết Từng Builder

### 2.1. `ToolAgentBuilder` (Tạo Agent Nhanh Không Cần Viết Graph)
Dành cho các tác nhân đơn giản chỉ xoay quanh việc gọi Tool. Nhà phát triển chỉ cần khai báo các công cụ với decorator `@tool`:

```python
from langchain_core.tools import tool
from agent_sdk.builders import ToolAgentBuilder

@tool
def search_customer_by_id(customer_id: str) -> str:
    """Tra cứu thông tin khách hàng từ hệ thống SAP MDG."""
    return f"Customer {customer_id}: Công ty Cổ phần ABC, TaxCode: 0102030405"

# Khởi tạo Tool Agent tự động với vòng lặp ReAct
agent = (
    ToolAgentBuilder()
    .with_model("gpt-4o")
    .with_system_prompt("Bạn là trợ lý tra cứu dữ liệu khách hàng.")
    .with_tools([search_customer_by_id])
    .build()
)
```
- **Cơ chế tự động:** Builder tự động cấu hình node `agent`, node `tools`, cạnh có điều kiện `tools_condition`, và quản lý lịch sử hội thoại.

---

### 2.2. `AgentGraphBuilder` (Fluent API Tùy Biến Đồ Thị)
Cung cấp cú pháp chuỗi (Fluent Interface) cho phép nhà phát triển toàn quyền kiểm soát cấu trúc đồ thị LangGraph:

```python
from agent_sdk.builders import AgentGraphBuilder

builder = (
    AgentGraphBuilder(state_schema=MyCustomState)
    .add_node("validate_input", validate_input_node)
    .add_node("plan_steps", plan_steps_node)
    .add_node("execute_tool", execute_tool_node)
    .add_node("format_output", format_output_node)
    
    # Thiết lập cạnh điều hướng
    .set_entry_point("validate_input")
    .add_edge("validate_input", "plan_steps")
    .add_conditional_edges(
        "plan_steps",
        routing_condition_fn,
        {
            "need_tool": "execute_tool",
            "done": "format_output"
        }
    )
    .add_edge("execute_tool", "plan_steps")  # Vòng lặp phản hồi
    .set_finish_point("format_output")
)

compiled_graph = builder.compile(checkpointer=hana_checkpointer)
```

---

### 2.3. `FlowGraphBuilder` (Đồ Thị Định Nghĩa Bằng Cấu Hình Declarative)
- Cho phép người quản trị thay đổi cấu trúc luồng của Agent thông qua file cấu hình JSON/YAML mà không cần sửa code hay triển khai lại container:
```yaml
flow_name: "customer_validation_flow"
entry_point: "sanitize_node"
nodes:
  - id: "sanitize_node"
    handler: "app.nodes.sanitize"
  - id: "llm_check_node"
    handler: "app.nodes.llm_check"
edges:
  - from: "sanitize_node"
    to: "llm_check_node"
```

---

### 2.4. `SubGraphAgentBuilder` (Đồ Thị Lồng Nhau / Subgraphs)
- Cho phép module hóa sâu: Một Agent lớn (ví dụ: `DataGovernanceSupervisor`) có thể chứa bên trong nó nhiều Subgraphs độc lập (`SchemaCheckSubgraph`, `DeduplicationSubgraph`).
- Mỗi Subgraph có không gian trạng thái riêng, giúp kiểm soát ngữ cảnh gọn gàng và dễ dàng tái sử dụng giữa các Agent khác nhau.
