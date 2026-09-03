# 04. Điều Phối Sub-Workflow & Quy Trình Con (Child Workflows)

> **Phân hệ:** AI Workflow Management  
> **Chủ đề:** Bộ điều phối `ChildWorkflowCoordinator`, vòng đời Sub-workflow con, cơ chế Input/Output Binding và phòng chống chu trình chéo (Cross-Workflow Cycles).

---

## 1. Giới Thiệu Về Node `WORKFLOW`

Trong các quy trình phức tạp của doanh nghiệp, việc phân rã một workflow khổng lồ thành các **Sub-workflow nhỏ hơn (Reusable Sub-flows)** là nguyên tắc thiết kế tối quan trọng. Node `WORKFLOW` cho phép nhúng một workflow khác vào trong quy trình hiện tại như một bước thực thi độc lập.

Quy trình con được quản lý bởi `ChildWorkflowCoordinator` (`layer2_application/orchestration/child_workflow_coordinator.py`).

---

## 2. Vòng Đời 3 Giai Đoạn Của Sub-Workflow

```mermaid
sequenceDiagram
    autonumber
    participant PE as Parent Engine
    participant CWC as ChildWorkflowCoordinator
    participant DB as HANA Repo
    participant CE as Child Engine (Independent Run)

    Note over PE: Node WORKFLOW chạm tới _exec_uniform
    PE->>CWC: start(node, parent_run, parent_task)
    
    rect rgb(240, 248, 255)
    Note over CWC: 1. Khởi Tạo Child Run
    CWC->>CWC: Kiểm tra chu trình (CrossWorkflowCycleError)
    CWC->>CWC: Ánh xạ biến qua input_binding
    CWC->>DB: Tạo bản ghi WorkflowRun mới (parent_run_id = parent.id)
    CWC->>DB: Đánh dấu parent_task = RUNNING
    CWC->>PE: Emit ChildRunStarted
    CWC->>CE: orchestrate_start(child_run.id)
    end

    Note over CE: Child Run tự do thực thi DAG riêng biệt...

    alt Child Run Thành Công (COMPLETED)
        rect rgb(230, 255, 230)
        Note over CWC: 2. Hoàn Tất Thành Công
        CE->>CWC: complete_parent(child_run, parent_run)
        CWC->>CWC: Thu thập output của child run
        CWC->>CWC: Ánh xạ output_binding vào parent_task
        CWC->>DB: parent_task.status = COMPLETED
        CWC->>PE: Emit NodeCompleted & ChildRunCompleted
        CWC->>PE: process_and_dispatch_successors(parent_task)
        end
    else Child Run Thất Bại (FAILED)
        rect rgb(255, 230, 230)
        Note over CWC: 3. Xử Lý Thất Bại
        CE->>CWC: fail_parent(child_run, parent_run)
        CWC->>PE: Emit ChildRunFailed
        alt on_failure == "skip"
            CWC->>DB: parent_task.status = COMPLETED (Success-equivalent)
            CWC->>PE: Tiếp tục DAG của parent qua cổng success
        else on_failure == "fail"
            CWC->>DB: parent_task.status = FAILED
            CWC->>PE: check_run_completion() đẩy lỗi lên parent
        end
        end
    end
```

---

## 3. Cơ Chế Ánh Xạ Biến (Input & Output Binding)

Sub-workflow hoạt động trong một không gian ngữ cảnh (Context) hoàn toàn độc lập với workflow cha:

### 3.1. Input Binding (Cha ➔ Con)
Trước khi khởi chạy con, `ChildWorkflowCoordinator` đọc cấu hình `input_binding` trên node `WORKFLOW` của cha:
```yaml
input_binding:
  customer_id: "{{ $fetch_order.data.customer_id }}"
  order_amount: "{{ $trigger.body.amount }}"
  processing_mode: "STRICT"
```
Các giá trị này được phân giải từ context của cha và nạp vào `child_run.input_data`, đóng vai trò là payload đầu vào cho node `TRIGGER` của con.

### 3.2. Output Binding (Con ➔ Cha)
Khi con hoàn thành, node `OUTPUT` của con sẽ tạo ra dữ liệu tổng kết. `ChildWorkflowCoordinator` sử dụng `output_binding` để map ngược lại vào context của cha:
```yaml
output_binding:
  approval_status: "{{ $child.output.status }}"
  invoice_number: "{{ $child.output.invoice_id }}"
```
Các biến này trở thành kết quả của node `WORKFLOW` trong cha, sẵn sàng để các node kế tiếp của cha sử dụng qua `{{ $workflow_node.data.approval_status }}`.

---

## 4. An Toàn Hệ Thống: Chu Trình Chéo & Giới Hạn Đệ Quy

1. **Phát hiện Chu Trình Chéo (`CrossWorkflowCycleError`):**
   - Nếu Workflow A gọi Workflow B, và Workflow B lại gọi ngược lại Workflow A, hệ thống sẽ bị treo vô tận.
   - Khi `start()` được gọi, coordinator duyệt chuỗi phả hệ `parent_run_id` ngược lên tận `root_run_id`. Nếu phát hiện `workflow_id` hiện tại đã từng xuất hiện trong cây gia phả của lần chạy này, hệ thống ném ngay lỗi `CrossWorkflowCycleError` và dừng lại an toàn.
2. **Giới Hạn Độ Sâu Tối Đa (`MAX_CHILD_DEPTH = 100`):**
   - Ngăn chặn việc lồng sub-workflow quá sâu làm cạn kiệt tài nguyên bộ nhớ.
