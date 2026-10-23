# Rete-Editor Context Analysis

Tài liệu này cung cấp phân tích chuyên sâu về component `rete-editor` trong dự án `ai-workflow-management`. Component được xây dựng phía trên thư viện [Rete.js (v2)](https://retejs.org/) để cung cấp một không gian làm việc Node-based linh hoạt, mạnh mẽ, hướng tới việc xây dựng và quản lý các luồng công việc AI (AI workflows).

## 1. Cấu trúc thư mục chi tiết (Directory Architecture)

Kiến trúc thư mục được thiết kế theo hướng Modular và Separation of Concerns (SoC), giúp việc mở rộng các loại Node mới hoặc bổ sung tính năng thao tác trên Editor dễ dàng:

```text
rete-editor/
├── config/           # Cấu hình thiết lập cho Node
│   └── node-config.ts     # Map `worker_type` với Component UI (Node rendering), định nghĩa size node.
│                          # Cung cấp enum `NODE_FACTORY_REGISTRY_KEY` cho các loại node đặc biệt (group_node).
├── connections/      # Chứa các component render cho đường kết nối (Connection)
│   ├── magnetic-connection/      # Logic tương tác "tự dính" (Magnetic) phức tạp được tách thành module:
│   │   ├── index.tsx, magnetic-connection.tsx, setup-magnetic-connection.tsx
│   │   └── math.ts, utils.ts, types.ts
│   ├── line-connection.tsx       # Vẽ đường SVG cho connection, tích hợp nút Delete.
│   ├── path-router.ts            # Logic tính toán đường đi (path) cho connection.
│   └── selectable-connection.tsx # Logic click để chọn đường kết nối.
├── core/             # Core Engine - Khởi tạo Editor instance và cài đặt các plugin của Rete.js
│   ├── editor.ts     # Main entry: Khởi tạo, gộp các API từ `operations` cung cấp `IEditorInstance`.
│   ├── plugins.tsx   # Tích hợp Area, Connection, React, AutoArrange và History plugins.
│   └── styles.ts     # Hàm cấu hình style CSS global cần thiết cho container chứa Editor.
├── nodes/            # Định nghĩa logic cơ bản của Node và các UI Component liên quan
│   ├── components/       # (MỚI) Tập hợp các React Components để render Node UI và Sockets
│   │   ├── base-node-shell.tsx  # Lớp vỏ UI chính cho BaseNode, bắt event dblclick, pointerdown.
│   │   ├── group-nodes.tsx      # Render cho GroupNode (container có viền dashed).
│   │   ├── socket-column.tsx, socket-row.tsx, socket-item.tsx # Component quản lý layout các Sockets (Input/Output).
│   │   ├── dynamic-node-icon.tsx # Component render icon linh hoạt.
│   │   └── loading-spin/         # Hiển thị icon trạng thái executing.
│   ├── base-node.ts      # Class BaseNode kế thừa ClassicPreset.Node, quản lý input/output sockets, state execution.
│   ├── group-node.ts     # Class GroupNode dành cho Container Nodes (vd: loop).
│   ├── socket.ts         # Khởi tạo singleton socket dùng chung cho Rete.
│   └── object-control.ts # Hỗ trợ Control elements của Rete nếu cần.
├── operations/       # Chứa logic xử lý tương tác phân tách theo Domain (Thao tác cụ thể)
│   ├── connection-operation.ts  # Thêm/Xóa/Lấy connections, cập nhật state connection theo node.
│   ├── group-operation.ts       # Logic thao tác Group: phát hiện overlap, join/leave group.
│   ├── nodes-operation.ts       # CRUD Node (Thêm/xoá/copy/vị trí) và Duyệt đồ thị (traversal).
│   ├── serialization.ts         # Logic Export/Import dữ liệu (Serializer/Deserializer) ra dạng JSON Schema.
│   └── view-operation.ts        # Can thiệp vào viewport (Zoom In/Out, Zoom to fit panel).
├── types/            # TypeScript interfaces / types cho toàn bộ module
│   └── index.ts      # Khai báo Rete Schemes, `IEditorInstance`, Schema truyền payload Node, Node Execution Status.
└── index.ts          # Public API export ra ngoài (The Facade Interface)
```

## 2. Phân tích chi tiết Kiến trúc Core (Core Architecture Deep-dive)

### 2.1. Quản lý Plugins trong `core/plugins.tsx` (`setupPlugins`)

Đây là nơi cấu hình tập trung tất cả các extensions/plugins của hệ sinh thái Rete.js:

-   **Data Model (`NodeEditor`)**: Nơi duy nhất chứa source-of-truth về cấu trúc Node và Connection.
-   **Rendering (`ReactPlugin`)**: Thay vì dùng mặc định, plugin này được cấu hình phần `customize` để:
    -   Quyết định render React Component nào dựa trên data `label` thông qua `getNodeFactory`. Node mặc định sẽ được render bằng `BaseNodeShell`.
    -   Tích hợp các Event Listener quan trọng như `onContextMenu` và `onDoubleClick`.
    -   Thay thế đường nối (Connection) mặc định bằng custom Component như `MagneticConnection` hoặc `SelectableConnectionBind`.
-   **Interaction (`AreaPlugin` & `AreaExtensions`)**:
    -   Cho phép chọn nhiều node (Selectable Nodes) với phím Ctrl.
    -   Thiết lập thứ tự hiển thị Node (Order).
-   **Layouting (`AutoArrangePlugin` & `Applier`)**: Áp dụng layout theo hướng (horizontal/vertical) tích hợp `popmotion` để tạo transition.
-   **History (`HistoryPlugin`)**: (Mới) Tích hợp undo/redo logic và phím tắt thông qua `HistoryExtensions.keyboard`.

### 2.2. Trích xuất UI Nodes (`nodes/components/`)

Thay vì để toàn bộ logic UI Node vào chung với data logic, kiến trúc mới tách riêng React Component vào thư mục `nodes/components/`:

-   **`BaseNodeShell`**: Xử lý dàn trang linh hoạt tùy luồng ngang (`horizontal`) hoặc dọc (`vertical`) thông qua `SocketRow` và `SocketColumn`. Quản lý viền theo trạng thái (executing, failed, completed).
-   # Các event pointer/dblclick cũng được cô lập vào phần shell để tránh xung đột với Area zooming của Rete.
-   **Layouting (`AutoArrangePlugin` & `Applier`)**: Sử dụng thuật toán tự động sắp xếp lại vị trí các nodes dạng luồng (Flow) cùng với thư viện `popmotion` để applier tạo hiệu ứng transition (animation `easeInOut`) mượt mà thay vì nhảy cóc.
-   **Graph Logging/Tracking (`rete-structures`)**: Khởi tạo cấu trúc dữ liệu đồ thị, giúp phân tích topology của Editor để truy xuất node đầu vào/đầu ra cực kỳ hiệu quả.

File này sử dụng **Facade Pattern** cực kỳ mạnh mẽ. Trọng tâm của nó là gói gọn mọi API phức tạp và phức hợp thành một Interface phẳng `IEditorInstance`.

### 2.3. Logic Domain qua các Operations (`operations/`)

Trình soạn thảo tách biệt logic thành 5 trụ cột để dễ bảo trì:

1.  **Nodes Operations (`nodes-operation.ts`)**: CRUD, Duyệt Graph, Execution State (`setNodeStatus`).
2.  **Connection Operations (`connection-operation.ts`)**: Quản lý status của connection khi node data thay đổi.
3.  **Group Operations (`group-operation.ts`)**: `getGroupAtPosition`, `joinGroup`, `leaveGroup`.
4.  **Serialization (`serialization.ts`)**: `serializeNodes` và `initialLoadNodes` xuất JSON Editor State.
5.  **View Operation (`view-operation.ts`)**: Bọc các hàm của `AreaExtensions` liên quan đến quản lý Viewport, giới hạn Zoom boundaries.

### 2.4. Tối ưu hoá việc Đồng bộ Trạng thái (Debouncing Editor Sync)

Hệ thống cung cấp một Custom Hook ngoài Editor là `useEditorSync` (nằm trong thư mục `hooks/` của Editor Module cha) để bắt event và đồng bộ trạng thái về Redux/State App.

-   **Vấn đề cũ**: Khi User Drag and Drop (thực hiện sự kiện kéo Node qua `translate`), rất nhiều event `nodetranslated` được emit trong một second. Việc gọi `serializeNodes` liên tục ở mỗi khung hình dẫn đến hiện tượng lag giật và sụt giảm hiệu năng.
-   **Giải pháp**: Xây dựng cơ chế **Debounce** nội tại nhờ việc lưu trữ một chu kỳ `setTimeout(..., 300)` tại hàm `nodetranslated` scope của Hook. Nếu người dùng tiếp tục kéo, sự kiện đồng bộ sẽ bị hủy và đếm lại. Trạng thái JSON thay đổi chỉ được cập nhật sau khi người dùng buông chuột đứng yên trong 300ms.

## 3. Quản lý Data Flow và Trạng thái Thực Thi (Execution State)

1. App gửi lệnh chạy Node.
2. Tại UI Container, gọi `editorInstance.updateNodeStatus(nodeId, "executing")`.
3. Hàm này chạy ngầm vào Node Operations để đổi properties Node logic.
4. Framework Rete kích sự kiện tới Area để update lại DOM qua Custom component (hiển thị `LoadingSpin`).

## 4. Group Node System (Hệ thống Container Node)

1. **Khởi tạo**: Check Enum Registry `GROUP_NODE_REGISTERED_TYPES` sinh `GroupNode`.
2. **Kéo thả Group**: Phát hiện overlap với logic Area, gọi `joinGroup`/`leaveGroup` tính toán `node.parent`.

## 5. Các mẫu thiết kế nội bộ (Design Patterns Applied)

1.  **Facade Pattern**: Giao tiếp UI qua interface phẳng `IEditorInstance`.
2.  **Plugin-based Architecture**: Khả năng lắp rắp các Core Middleware (React rendering, History events).
3.  **Command/Operations Separation**: State Updates tách rời Logic Model.
4.  **Debounce Optimization Pattern**: Trong `useEditorSync`, giảm tải render payload dư thừa phía ngoài.

## 6. Quy trình mở rộng (Extension Workflow)

1.  **Sửa UI Render Code**: Đối với Node UI, chỉnh code tại `nodes/components/`.
2.  **Thúc đẩy API Mới**: Nếu thêm API vào Rete (như Add connection thủ công), viết tại `operations`, bọc ở `editor.ts` return object.
3.  **Thay đổi Magnetic Drag**: File math và logic cho tương tác connection đặc biệt đã được module hoá và tập trung toàn bộ ở thư mục `connections/magnetic-connection/`.
