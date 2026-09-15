import { defineConfig } from 'vitepress'

// VitePress configuration for the AI Workflow Management user guide.
// https://vitepress.dev/reference/site-config
export default defineConfig({
  title: 'Workflow Engine',
  description:
    'User guide for the AI Workflow Management engine — every node, how to set it up, and how to run a workflow.',
  lang: 'en-US',
  lastUpdated: true,
  cleanUrls: true,
  ignoreDeadLinks: false,

  markdown: {
    // The docs are full of {{$node.output.field}} expression examples. Vue treats
    // {{ }} as template interpolation even inside inline code, which both renders
    // them blank and breaks the build on non-JS tokens (e.g. the `...` ellipsis).
    // Fenced code blocks are already v-pre; here we make inline `code` spans v-pre
    // too, so every mustache renders literally.
    config: (md) => {
      md.renderer.rules.code_inline = (tokens, idx, _options, _env, self) => {
        const token = tokens[idx]
        return `<code v-pre${self.renderAttrs(token)}>${md.utils.escapeHtml(
          token.content,
        )}</code>`
      }
    },
  },

  themeConfig: {
    nav: [
      { text: 'Guide', link: '/guide/introduction' },
      { text: 'Nodes', link: '/nodes/' },
      {
        text: 'Reference',
        items: [
          { text: 'Workers', link: '/reference/workers' },
          { text: 'REST API', link: '/reference/rest-api' },
          { text: 'Node Cheat Sheet', link: '/reference/cheatsheet' },
        ],
      },
    ],

    sidebar: {
      '/guide/': [
        {
          text: 'Getting Started',
          items: [
            { text: 'Introduction', link: '/guide/introduction' },
            { text: 'Core Concepts', link: '/guide/concepts' },
            { text: 'Architecture', link: '/guide/architecture' },
          ],
        },
        {
          text: 'Setup',
          items: [
            { text: 'Installation & Run', link: '/guide/setup' },
            { text: 'Workers', link: '/guide/workers-setup' },
          ],
        },
        {
          text: 'Building Workflows',
          items: [
            { text: 'Running a Workflow', link: '/guide/running-a-workflow' },
            { text: 'Expressions & Variables', link: '/guide/expressions' },
            { text: 'Run Lifecycle & Control', link: '/guide/lifecycle' },
            {
              text: 'The Orchestration Engine',
              link: '/guide/orchestration-engine',
            },
          ],
        },
      ],

      '/nodes/': [
        {
          text: 'Nodes',
          items: [
            { text: 'Overview', link: '/nodes/' },
            { text: 'Entry & Output', link: '/nodes/entry-output' },
            { text: 'Branching & Routing', link: '/nodes/branching' },
            { text: 'Loops', link: '/nodes/loops' },
            { text: 'Concurrency', link: '/nodes/concurrency' },
            { text: 'Composition', link: '/nodes/composition' },
            { text: 'Transform & Variables', link: '/nodes/transform' },
            { text: 'Human-in-the-Loop', link: '/nodes/human-in-the-loop' },
            { text: 'Integration', link: '/nodes/integration' },
            { text: 'Task & Workers', link: '/nodes/task' },
          ],
        },
      ],

      '/reference/': [
        {
          text: 'Reference',
          items: [
            { text: 'Workers', link: '/reference/workers' },
            { text: 'REST API', link: '/reference/rest-api' },
            { text: 'Node Cheat Sheet', link: '/reference/cheatsheet' },
          ],
        },
      ],
    },

    socialLinks: [],

    search: {
      provider: 'local',
    },

    outline: {
      level: [2, 3],
    },

    docFooter: {
      prev: true,
      next: true,
    },
  },
})
