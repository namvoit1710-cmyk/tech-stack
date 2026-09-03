# 01. Tổng Quan & Các Khái Niệm Cốt Lõi (Overview & Core Concepts)

> **Phân hệ:** AI Agent Subsystem  
> **Chủ đề:** Mục đích kiến trúc, nền tảng LangGraph, và sự phối hợp giữa Agent và Workflow.

---

## 1. Bối Cảnh & Mục Đích Của Phân Hệ Agent

Bên cạnh nền tảng điều phối quy trình xác định (**AI Workflow Management**), hệ thống SimpleMDG xây dựng một phân hệ **AI Agent Ecosystem** toàn diện tại `apps/backend/agent`. Đây là nơi vận hành các **Tác nhân AI tự trị (Autonomous & Conversational AI Agents)** dựa trên khung nền tảng [LangGraph](https://langchain-ai.github.io/langgraph/) kết hợp [FastAPI](https://fastapi.tiangolo.com/).

Các Agent này đóng vai trò là "chuyên gia số" trong từng nghiệp vụ cụ thể của nền tảng quản trị dữ liệu tổng thể (Master Data Governance - MDG):
- Hiểu ngôn ngữ tự nhiên từ người dùng (User Intents & Natural Language Prompts).
- Tự động phân rã mục tiêu phức tạp thành kế hoạch hành động (Plan & Decomposition).
- Lựa chọn và kích hoạt các công cụ (Tool Calling / Function Calling).
- Tự động sinh ra cấu hình workflow, thiết lập luật kiểm tra dữ liệu (Validation Rules), quản lý schema và chẩn đoán sự cố hệ thống.

---

## 2. So Sánh: AI Workflow vs. AI Agent

Một trong những nguyên tắc thiết kế quan trọng nhất của SimpleMDG là **phân định ranh giới rõ ràng** giữa Workflow và Agent:

| Tiêu Chí So Sánh | AI Workflow Management | AI Agent Subsystem |
|---|---|---|
| **Mô hình cốt lõi** | Event-Driven Directed Acyclic Graph (DAG) | Đồ thị trạng thái tuần hoàn (Cyclic StateGraph / LangGraph) |
| **Bản chất thực thi** | **Xác định (Deterministic):** Luồng đi theo các wire/edge định sẵn, điều kiện boolean rõ ràng. | **Tự trị (Autonomous):** Mô hình LLM tự quyết định bước tiếp theo dựa trên quan sát (ReAct pattern). |
| **Mục đích sử dụng** | Điều phối pipeline dữ liệu lớn, chạy hàng loạt (batch), xử lý ETL, giao tiếp dịch vụ nội bộ. | Giao diện hội thoại (Chatbot), giải quyết bài toán mờ, sáng tạo nội dung, hỗ trợ người dùng. |
| **Cơ chế lưu trạng thái**| Run Context (`run.context.variables`) + HANA Event Log. | Checkpointing State (`AgentBaseState`) + Concurrency Checkpointer. |
| **Mức độ phụ thuộc LLM**| LLM chỉ là 1 node (`TASK` với `agent-worker` hoặc node `PROMPT`). | LLM là hạt nhân điều khiển (Central Brain) của toàn bộ đồ thị lặp. |

### Mô Hình Phối Hợp Hiệp Lực (Synergy)
```
[Người Dùng Hội Thoại]
         │ (Ngôn ngữ tự nhiên: "Hãy tạo luồng import khách hàng từ file CSV")
         ▼
[Workflow Designer Agent (AI Agent Subsystem)]
         │ (Tự động thiết kế, chọn node, sinh kết nối wires)
         ▼
[AI Workflow Management (Control Plane)]
         │ (Thực thi pipeline dữ liệu tốc độ cao, phân tán, bền vững)
         ▼
[Kết Quả Dữ Liệu Trong SAP S/4HANA]
```

---

## 3. Khung Nền Tảng: LangGraph + 4-Layer Clean Architecture

Mỗi Agent trong hệ thống được xây dựng trên **LangGraph** — thư viện hàng đầu cho phép xây dựng các Agent có trạng thái (Stateful Multi-Actor Applications):
- **Trạng thái (State):** Đại diện bởi class kế thừa `AgentBaseState`, truyền qua từng node trong đồ thị.
- **Node:** Là một hàm Python thuần túy: `(state, deps) -> partial_update`.
- **Edge:** Quyết định đường đi: Cạnh cố định (Normal Edge) hoặc Cạnh điều kiện (Conditional Edge) dựa trên quyết định của LLM (ví dụ: gọi Tool tiếp hay kết thúc trả lời).
- **Vòng lặp ReAct:** Cho phép Agent lặp: *Suy nghĩ (Reason) ➔ Hành động (Action: Tool) ➔ Quan sát (Observation)* cho đến khi giải xong bài toán.

---

## 4. Các Thành Phần Chính Của Phân Hệ

1. **`agent-sdk/`:** Bộ SDK nền tảng dùng chung cho tất cả các Agent, đóng gói sẵn kết nối HTTP, Kafka/Event Mesh, Checkpoint HANA, và Context Budget Manager.
2. **`orchestrator/`:** Bộ điều phối hội thoại đa tác nhân (Multi-Agent Supervisor), tích hợp Guardrails (kiểm duyệt đầu vào/ra), Intent Classifier và Planner.
3. **`agent-registry/`:** Dịch vụ quản lý danh mục Agent, kiểm tra sức khỏe và hỗ trợ khám phá động (Dynamic Discovery).
4. **`executor-runtime-service/`:** Môi trường runtime thực thi các tác vụ Agent.
5. **Hệ sinh thái Domain Agents:** Các agent chuyên sâu nghiệp vụ:
   - `workflow-designer-agent`: Tự động thiết kế AI Workflow.
   - `governance-schema-agent`: Quản trị schema và cấu trúc dữ liệu.
   - `validation-rule-agent`: Tự động sinh luật kiểm thử dữ liệu.
   - `profile-manager-agent`: Đánh giá hồ sơ và chất lượng dữ liệu.
   - `csv-agent` & `file-agent`: Xử lý tài liệu và bảng tính.
   - `troubleshooting-agent`: Tự động chẩn đoán lỗi và gỡ lỗi hệ thống.
   - `business-agent`: Tác nhân kinh doanh tùy biến.
6. **`laidonllm/`:** Tầng Gateway kết nối mô hình LLM nội bộ.
