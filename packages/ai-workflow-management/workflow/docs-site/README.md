# Workflow Engine — User Guide (VitePress)

A documentation site that guides users through every node in the workflow engine, how to set the system up, and how to run a workflow.

## Run it

```bash
npm install        # first time
npm run docs:dev   # live dev server at http://localhost:5173
npm run docs:build # static build → docs/.vitepress/dist
npm run docs:preview
```

## What's inside

```
docs/
  index.md              Home page
  guide/                Getting started, setup, building workflows
    introduction.md     What the engine is and supports
    concepts.md         Vocabulary (workflow, node, port, run, …)
    architecture.md     Control plane / executor / workers + infra modes
    setup.md            Install & run the services
    workers-setup.md    Run & register workers
    running-a-workflow.md   End-to-end REST walkthrough
    expressions.md      {{$...}} expression syntax
    lifecycle.md        Run states + pause/resume/cancel/rerun
  nodes/                Reference for all 19 node types, grouped by category
  reference/            Workers, REST API, and a one-page cheat sheet
```

## Note on `{{ ... }}` in Markdown

The docs are full of `{{$node.output.field}}` expression examples. VitePress is
Vue-based and treats `{{ }}` as template interpolation — even inside inline code —
which renders them blank and can break the build on non-JS tokens. The config
(`docs/.vitepress/config.mts`) makes all inline `code` spans `v-pre`, so mustaches
render literally. Fenced code blocks are already `v-pre` by default. If you add
mustaches in **plain prose** (outside backticks and fences), wrap them in backticks.

## Keeping it accurate

The node and worker tables are derived from the backend source:

- Node specs: `workflow-control-plane-service/app/layer1_domain/node_spec/`
- Handler registry: `workflow-control-plane-service/bootstrap.py`
- Workers: `worker/*/` (each worker's `execute_task` use case defines its schema)
- REST endpoints: `workflow-control-plane-service/app/layer3_adapters/controllers/restful/v1/`

When those change, update the corresponding page.
