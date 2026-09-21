# CSV Agent Documentation

Complete documentation and diagrams for the CSV Agent architecture, workflows, and deployment.

## 📚 Documentation Index

### 📖 Main Documentation
- **[README.md](../README.md)** - Complete user guide, API reference, and getting started
  - Overview & Key Capabilities
  - Installation & Configuration
  - API Reference
  - Development Guide
  - Troubleshooting

### 📊 Architecture Diagrams (PlantUML)

All diagrams are in PlantUML format (`.puml`). Render them using:

```bash
# Install PlantUML
brew install plantuml  # macOS
# or download from https://plantuml.com

# Render diagrams
plantuml docs/*.puml

# Generates PNG/SVG files
```

---

## 🎨 Available Diagrams

### 1. **Sequence Diagram** 
📄 [sequence-diagram.puml](sequence-diagram.puml)

**Shows:** Complete request-to-response flow through the system

**Key Flows:**
- Client request → API → UseCase → Graph
- Planning phase (LLM generates execution plan)
- Execution phase (tool invocation with auto-mapping)
- Review phase (success validation)
- Conditional routing (loop vs end)
- MCP vs Internal tool differentiation

**Best For:** Understanding runtime behavior and message flow

**Preview:**
```
Client → API → ExecuteAgentUseCase → Graph
  → PlanNode → PlanUseCase → OpenAI
  → ExecuteNode → Tools (MCP/Internal) → Data Factory/File Service
  → ReviewNode → ReviewUseCase
  → Conditional (loop or end)
  → Response
```

---

### 2. **Class Diagram**
📄 [class-diagram.puml](class-diagram.puml)

**Shows:** Complete class structure with relationships

**Key Components:**
- **Domain Layer**: FileState, ExecutionPlan, ExecutionResult, StepResult
- **Application Layer**: Use Cases, Graph Nodes, Tool Wrappers, Features
- **Framework Layer**: Config, DI Container, Adapters, Tool Registry

**Relationships:**
- Composition (●—): Ownership (ExecutionPlan has ExecutionSteps)
- Dependency (..>): Uses (PlanNode uses PlanUseCase)
- Inheritance (◁—): Extends (Settings extends SDK Settings)

**Best For:** Understanding code structure and dependencies

---

### 3. **Architecture Overview**
📄 [architecture-overview.puml](architecture-overview.puml)

**Shows:** High-level component architecture with external services

**Key Elements:**
- Layered architecture (Presentation → Application → Domain → Infrastructure)
- Workflow engine (Plan → Execute → Review)
- Tool architecture (Internal vs MCP)
- External service integration (OpenAI, File Service, Data Factory)
- Configuration and dependency injection

**Best For:** Onboarding new team members and architecture discussions

**Preview:**
```
┌────────────────────────────────┐
│  Presentation (Agent SDK)      │
├────────────────────────────────┤
│  Application (Graph + Tools)   │
├────────────────────────────────┤
│  Domain (Entities)             │
├────────────────────────────────┤
│  Frameworks (Adapters + DI)    │
└────────────────────────────────┘
        ↓          ↓         ↓
    OpenAI   File Service  Data Factory
```

---

### 4. **Component Diagram**
📄 [component-diagram.puml](component-diagram.puml)

**Shows:** Component dependencies and layering rules

**Key Features:**
- Layer isolation (Domain has no dependencies)
- Dependency flow (Application → Domain, Application → Frameworks)
- Component relationships within each layer
- External service boundaries
- Bootstrap and composition root

**Best For:** Understanding dependency rules and potential refactoring

**Dependency Rules:**
```
Layer 1 (Domain)      → No dependencies
Layer 2 (Application) → Layer 1, Layer 4
Layer 4 (Frameworks)  → External Services
```

---

### 5. **Tool Interaction Flow**
📄 [tool-interaction-flow.puml](tool-interaction-flow.puml)

**Shows:** Detailed tool selection and execution logic

**Key Flows:**
- Parameter-first tool selection logic
- Auto-serialization (dict → JSON string)
- Auto-mapping (message → long_input)
- MCP vs Internal tool routing
- Error handling and fallback strategies
- Conditional re-planning

**Best For:** Understanding tool invocation mechanics and debugging tool selection issues

**Decision Points:**
```
Has parameters? → Yes → Parameter-first selection
                  No → Message keyword matching

Tool type? → Contains "_mcp" → MCP Tool → Data Factory
             Otherwise → Internal Tool → Local execution
```

---

### 6. **Deployment Diagram**
📄 [deployment-diagram.puml](deployment-diagram.puml)

**Shows:** Production deployment architecture

**Key Elements:**
- Service topology (CSV Agent, Data Factory, File Service)
- Port mappings and network policies
- Resource requirements (CPU, memory)
- External dependencies (OpenAI, S3)
- Observability stack integration
- High availability configuration

**Best For:** DevOps, deployment planning, infrastructure provisioning

**Services:**
```
CSV Agent (Port 37001)
  ↓
Data Factory (Port 8088) + File Service (Port 8080)
  ↓
S3 Storage + OpenAI API
```

---

## 🎯 Diagram Selection Guide

**I want to understand...**

| Goal | Recommended Diagram |
|------|---------------------|
| How a request flows through the system | [Sequence Diagram](sequence-diagram.puml) |
| Code structure and classes | [Class Diagram](class-diagram.puml) |
| Overall architecture | [Architecture Overview](architecture-overview.puml) |
| Component dependencies | [Component Diagram](component-diagram.puml) |
| Tool selection logic | [Tool Interaction Flow](tool-interaction-flow.puml) |
| Deployment setup | [Deployment Diagram](deployment-diagram.puml) |

---

## 🛠️ Rendering Diagrams

### Online Rendering

**PlantUML Web Server:**
- Visit: http://www.plantuml.com/plantuml/uml/
- Paste diagram content
- View rendered diagram

**VS Code Extension:**
- Install: "PlantUML" by jebbs
- Open `.puml` file
- Press `Alt+D` to preview

### Local Rendering

```bash
# Install PlantUML
brew install plantuml  # macOS
apt-get install plantuml  # Ubuntu
choco install plantuml  # Windows

# Render all diagrams
cd apps/backend/agent/csv-agent/docs
plantuml *.puml

# Render specific diagram
plantuml sequence-diagram.puml

# Generate SVG (scalable)
plantuml -tsvg *.puml

# Generate PNG with high DPI
plantuml -Sdpi=300 *.puml
```

### Export Formats

PlantUML supports multiple output formats:
- **PNG** - Raster image (default)
- **SVG** - Scalable vector graphics (recommended for web)
- **PDF** - Printable documents
- **LaTeX** - Academic papers
- **ASCII Art** - Terminal/text documentation

---

## 📝 Diagram Conventions

### Colors
- 🟢 **Green (#DDFFDD)** - Layer 1: Domain (pure business logic)
- 🔵 **Blue (#DDDDFF)** - Layer 2: Application (use cases, orchestration)
- 🟡 **Yellow (#FFFFDD)** - Layer 4: Frameworks (infrastructure)
- 🔴 **Red (#FFDDDD)** - External services/SDK

### Symbols
- **→** Synchronous call
- **..>** Dependency/Uses
- **●—** Composition (owns)
- **◁—** Inheritance (extends)
- **◆—** Aggregation (has)

### Notes
- 📌 Important implementation details
- ⚠️ Warning/Limitation
- ✅ Success case
- ❌ Error case
- 🔌 MCP Tool
- ⚙️ Internal Tool

---

## 🔄 Keeping Diagrams Updated

**When to update:**
- Adding new tools
- Changing workflow nodes
- Modifying architecture layers
- Adding external service integrations
- Changing deployment configuration

**Update checklist:**
1. Identify affected diagrams (see guide above)
2. Update `.puml` source files
3. Re-render diagrams
4. Update README.md if API changes
5. Commit both `.puml` and rendered images

---

## 📧 Contact & Support

For questions about architecture or diagrams:
- Check [README.md](../README.md) first
- Review diagram source code (`.puml` files are readable)
- Ask SMDG AI Team

---

**Built with PlantUML | Last Updated: May 2026**
