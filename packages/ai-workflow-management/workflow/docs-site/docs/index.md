---
layout: home

hero:
  name: AI Workflow Engine
  text: Build, run, and orchestrate workflows
  tagline: A node-based engine for control flow, data transformation, human-in-the-loop steps, and external integrations — orchestrated by a control plane and executed by pluggable workers.
  actions:
    - theme: brand
      text: Get Started
      link: /guide/introduction
    - theme: alt
      text: Browse Nodes
      link: /nodes/
    - theme: alt
      text: REST API
      link: /reference/rest-api

features:
  - icon: 🧩
    title: 19 Node Types
    details: Triggers, branching, loops, parallel/merge, sub-workflows, compute, prompts, variables, human steps, and worker-backed tasks — every node documented with its config and ports.
    link: /nodes/
    linkText: See all nodes
  - icon: 🔌
    title: Pluggable Workers
    details: TASK nodes dispatch to external workers — HTTP, AI agents, data mapping, waits, database, MS Teams, and gateway functions. Register your own over the worker SDK.
    link: /reference/workers
    linkText: Worker reference
  - icon: 🙋
    title: Human-in-the-Loop
    details: Pause a run to collect input, approve/reject, edit tabular data in place, or upload a file — then resume exactly where it left off.
    link: /nodes/human-in-the-loop
    linkText: HITL nodes
  - icon: 🧮
    title: Expressions & Variables
    details: Reference upstream node outputs, run metadata, loop indices, and run-scoped variables with expression syntax anywhere a field accepts them.
    link: /guide/expressions
    linkText: Expression syntax
  - icon: 🚦
    title: Full Run Control
    details: Start, pause, resume, cancel, rerun, and replay runs over a REST API. Real-time events stream over Socket.IO.
    link: /guide/lifecycle
    linkText: Lifecycle & control
  - icon: ⚙️
    title: Run It Locally
    details: A mock infrastructure mode runs the entire control plane with zero external dependencies. Scale up to Kafka and SAP HANA when you are ready.
    link: /guide/setup
    linkText: Setup guide
---
