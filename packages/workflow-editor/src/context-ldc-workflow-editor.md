# LDC Workflow Editor Context Analysis

Tài liệu này cung cấp cái nhìn tổng quan và kiến trúc thiết kế của module `ldc-workflow-editor`. Đây là một **Smart Wrapper Component** (vỏ bọc ứng dụng) chịu trách nhiệm tích hợp core engine Rete.js (`rete-editor`) vào hệ thống UI React/Next.js của dự án.

Mục tiêu chính của Component này là **ẩn đi sự phức tạp của Rete.js**, cung cấp các tính năng cấp cao (high-level features) như kéo thả (DnD), Copy/Paste, Toolbar, Context Menu, và quản lý vòng đời của Editor.

## 1. Cấu trúc thư mục (Directory Architecture)

```text
ldc-workflow-editor/
├── components/
│   ├── rete-editor/     # Core Engine xử lý Node/Graph/Canvas dựa trên Rete.js (Xem `context-rete-editor.md`).
│   └── workflow-editor/ # Các Component UI bao bọc xung quanh Canvas.
│       ├── node-context-menu/ # Custom Menu khi chuột phải vào Node.
│       ├── toolbar/           # SideToolbar (Kéo thả node, công cụ undo/redo), BottomToolbar (Kính lúp zoom, fit view).
│       └── workflow-editor.tsx # Main Entry Component, cầu nối giữa UI và Rete.
├── constants/           # Định nghĩa các hằng số dùng chung (Kích thước node mặc định, enum types).
├── hooks/               # Các Custom React Hook chịu trách nhiệm xử lý logic độc lập (Separation of Concerns).
│   ├── use-editor-clipboard.ts # Xử lý Copy/Paste JSON thông qua Clipboard API có validate data.
│   ├── use-editor-dnd.ts       # Xử lý logic Drag-and-Drop thả node mới từ menu vào canvas.
│   ├── use-editor-history.ts   # Quản lý history Undo/Redo (nếu có bổ sung).
│   ├── use-editor-setup.ts     # Khởi tạo `editorInstance` và gắn Rete vào thẻ div chứa (containerRef).
│   └── use-editor-sync.ts      # Bắt sự kiện thay đổi dữ liệu (debounced) từ Rete báo ra component cha.
├── i18n/                # Localization / Dịch thuật ngôn ngữ cho các message alert, tooltip, UI.
├── utils/               # Các hàm tiện ích (Tạo UUID, xử lý string, validate schema JSON).
├── index.ts             # Export Public API (Tránh việc bên ngoài import trực tiếp từ file sâu bên trong).
└── context-ldc-workflow-editor.md # (File này) Tài liệu kiến trúc cấp cao.
```

## 2. Phân tích chi tiết Kiến trúc (Architecture Deep-dive)

### 2.1. Main Component: `WorkflowEditor` (`workflow-editor.tsx`)

Đây là trung tâm giao tiếp (Facade Pattern) đại diện cho toàn bộ luồng xử lý:

-   **UI Composition**: Trình bày một giao diện chia theo Layout hoàn chỉnh: Viewport chính chứa Rete Canvas, `SideToolBar` bên trái/phải, `BottomToolbar` để thu phóng và `NodeContextMenu` dạng popup floating.
-   **Ref Forwarding (`useImperativeHandle`)**: Component được bọc bằng `forwardRef`, xuất khẩu một Object `WorkflowEditorHandle` cung cấp chính xác các API cần thiết cho App cha sử dụng mà không làm rò rỉ (leak) Rete Engine. Các methods bao gồm:
    -   Tác động Data: `addNode`, `addInputSocket`, `updateOutputSocketLabel`...
    -   Duyệt Graph: `getPredecessorNodes`, `getOutGoerNodes`, `getIncomerNodes`.
    -   Flow Execution: `setNodeStatus` (Để đánh dấu loading/thành công/thất bại khi backend chạy Node).
-   **Callback Inversion**: Component nhận vào các Event Props (như `onChange`, `onNodeSelected`, `onExecuteNode`, `onOpenNodePopup`) và truyền ngược lên Controller cha, duy trì tính "Dumb Controller" - Nó xử lý việc thao tác mượt mà chứ không chứa Business Logic của Luồng chạy AI.

### 2.2. Xử lý Logic thông qua Hệ thống Hooks

Bởi vì `WorkflowEditor` có quá nhiều chức năng vệ tinh, toàn bộ logic được bóc tách vào các tệp Hook độc lập:

1. **`useEditorSetup(value, options)`**:

    - Quản lý quá trình render Rete vào DOM Node cục bộ (`ref`).
    - Khởi tạo Data Model ban đầu (`value` truyền vào).
    - Truyền các API UI callback xuống cấp Core Engine (ví dụ: `openNodeContext` để Rete kích hoạt Custom Context Menu khi user click chuột phải).

2. **`useEditorSync(editorInstance, options)`**:

    - **Performance Focus**: Áp dụng cơ chế **Debouncing** (trì hoãn 300ms) trên các sự kiện dịch chuyển tọa độ (`translate`) của Rete, tránh việc serialize (render data ra JSON) một cách liên tục hàng chục lần một giây gây crash app.
    - Khi ổn định, nó sẽ gọi `onChange()` báo lại Redux/State App để lưu lại Database.

3. **`useEditorDnd(editorInstance, readOnly)`**:

    - Lắng nghe sự kiện kéo thả từ SideToolbar (chuẩn DOM HTML Drag and Drop).
    - Hiển thị Element mờ dạng Preview (`dragPreview`) tại con trỏ chuột khi kéo qua Vùng biên Editor.
    - Khi Thả (Drop) -> Tính toán tọa độ bù trừ Zoom/Offset (`transform.x`, `transform.k`) thông qua API `getTransform()` của Rete và chạy Pipeline Auto-Add Node vào vị trí chính xác.

4. **`useEditorClipboard({ editorInstance, onChange })`**:
    - Chặn hệ điều hành Copy/Paste để xử lý Serialization cho Node Graph.
    - Thêm UX (Toast Message qua thư viện dịch `i18n` và UI).
    - Có cơ chế **Validiate Schema Data (`validateEditorValue`)** báo lỗi lập tức nếu User copy từ File Text (non-JSON) tào lao hoặc Workflow không cùng mô hình, ngăn chặn sập Engine.

### 2.3. Tương tác View (Editor Viewport Features)

-   **Context Menu (`NodeContextMenu`)**: Chỉ bật lên khi có event truyền từ plugin React của Rete. Gọi các thao tác phổ thông như Delete, Copy, Open Detail Panel, và Execute (Run Node).
-   **Read-Only Mode**: Hỗ trợ Viewport không thể chỉnh sửa (`readOnly = true`) qua Prop để thiết kế màn hình History / Report cho workflow đã chạy xong. Khóa Dnd, Context Menu, và Clipboard khi bật cờ này.

## 3. Data Flow Overview (Luồng dữ liệu)

1. **Khởi tạo Load (`value`)**: `App` -> Truyền JSON state `IEditorValue` -> `WorkflowEditor` -> Hook `useEditorSetup` -> Rete Plugin (`initialLoadNodes`).
2. **Lưu dữ liệu thao tác**: User kéo thả / xóa dây trong Rete Core -> Hook `useEditorSync` bắt Event -> Đợi Debounce -> Callback `onChange` bắn JSON mới ra -> `App` lưu State.
3. **Thực thi Workflow (Execution)**: User click chuột phải "Execute" trên `NodeContextMenu` -> Emit event `onExecuteNode(nodeId)` báo cho App Backend xử lý -> Trả REST API về -> App đẩy ngược Prop API gọi qua `ref.current.setNodeStatus('nodeId', 'executing'|'completed')` -> Editor bốc cháy màu xanh báo tình trạng.

## 4. Tổng Kết Cách Mở Rộng (Extension Guide)

-   **Nếu thêm Công Cụ Button mới ngoài màn hình đen (Canvas)**: Bạn hãy vào thư mục `components/workflow-editor/toolbar/` sửa UI, và dùng `editorInstance` (truyền qua Props) để ra lệnh cho Core Canvas hoạt động.
-   **Nếu cần Expose Method cho Redux/Zustand gọi từ ngoài vào**: Thêm định nghĩa vào `WorkflowEditorHandle` (trong `workflow-editor.tsx`) và gài logic bằng `editorInstance`.
-   **Nếu phát triển UI phức tạp (Popup, Form)**: Đừng viết thẳng vào thư mục `rete-editor`, hãy viết tại thư mục `components/workflow-editor` và trigger thông qua props / hooks. Mọi State Dialog nên phơi bày ngược lại cho ứng dụng để quản lý thông qua event (VD: `onOpenNodePopup`).
