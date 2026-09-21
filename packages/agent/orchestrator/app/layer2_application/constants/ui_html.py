AGENT_DISPATCH_UI_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Agent Dispatch UI</title>
    <style>
        :root {
            color-scheme: light;
            --bg: linear-gradient(135deg, #f4efe6 0%, #dfe8f3 100%);
            --card: rgba(255, 255, 255, 0.9);
            --border: rgba(15, 23, 42, 0.14);
            --text: #172033;
            --muted: #5a6475;
            --accent: #0f766e;
            --accent-strong: #115e59;
        }

        * { box-sizing: border-box; }
        body {
            margin: 0;
            min-height: 100vh;
            font-family: "Segoe UI", sans-serif;
            background: var(--bg);
            color: var(--text);
            display: grid;
            place-items: center;
            padding: 24px;
        }

        .shell {
            width: min(960px, 100%);
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 24px;
            padding: 28px;
            box-shadow: 0 20px 60px rgba(15, 23, 42, 0.12);
            backdrop-filter: blur(12px);
        }

        h1 { margin: 0 0 8px; font-size: 2rem; }
        p { margin: 0 0 20px; color: var(--muted); }
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 20px;
        }
        .panel {
            border: 1px solid var(--border);
            border-radius: 18px;
            padding: 18px;
            background: rgba(255, 255, 255, 0.72);
        }
        label {
            display: block;
            font-size: 0.9rem;
            font-weight: 600;
            margin-bottom: 8px;
        }
        select, textarea, input, button {
            width: 100%;
            border-radius: 12px;
            border: 1px solid var(--border);
            padding: 12px 14px;
            font: inherit;
        }
        textarea {
            min-height: 140px;
            resize: vertical;
        }
        button {
            cursor: pointer;
            border: none;
            background: var(--accent);
            color: white;
            font-weight: 700;
            transition: background 0.2s ease, transform 0.2s ease;
        }
        button:hover { background: var(--accent-strong); transform: translateY(-1px); }
        button.secondary {
            background: #1d4ed8;
        }
        .stack { display: grid; gap: 14px; }
        .status {
            min-height: 24px;
            color: var(--muted);
            font-size: 0.95rem;
        }
        pre {
            margin: 0;
            padding: 16px;
            border-radius: 14px;
            background: #111827;
            color: #e5eefc;
            overflow: auto;
            min-height: 280px;
            white-space: pre-wrap;
            word-break: break-word;
        }
        .badge {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            margin-bottom: 12px;
            padding: 6px 10px;
            border-radius: 999px;
            background: rgba(15, 118, 110, 0.12);
            color: var(--accent-strong);
            font-size: 0.8rem;
            font-weight: 700;
        }
    </style>
</head>
<body>
    <main class="shell">
        <div class="badge">Orchestrator Agent Dispatch</div>
        <h1>Manual Agent Picker</h1>
        <p>The page loads published agents on open, dispatches through the existing API, and fetches the saved shared-state task by conversation id.</p>
        <div class="grid">
            <section class="panel stack">
                <div>
                    <label for="agentSelect">Available agents</label>
                    <select id="agentSelect"></select>
                </div>
                <div>
                    <label for="conversationId">Conversation id</label>
                    <input id="conversationId" placeholder="Optional. Leave empty to auto-generate." />
                </div>
                <div>
                    <label for="messageInput">Message</label>
                    <textarea id="messageInput" placeholder="Type a message for the selected agent"></textarea>
                </div>
                <button id="dispatchButton" type="button">Dispatch message</button>
                <button id="resultButton" class="secondary" type="button">Get result</button>
                <div id="status" class="status">Loading available agents...</div>
            </section>
            <section class="panel stack">
                <div>
                    <label>Shared-state result</label>
                    <pre id="resultOutput">No result loaded yet.</pre>
                </div>
            </section>
        </div>
    </main>
    <script>
        const agentSelect = document.getElementById("agentSelect");
        const messageInput = document.getElementById("messageInput");
        const conversationIdInput = document.getElementById("conversationId");
        const dispatchButton = document.getElementById("dispatchButton");
        const resultButton = document.getElementById("resultButton");
        const status = document.getElementById("status");
        const resultOutput = document.getElementById("resultOutput");

        function setStatus(message) {
            status.textContent = message;
        }

        function selectedAgent() {
            const raw = agentSelect.value;
            return raw ? JSON.parse(raw) : null;
        }

        async function loadAgents() {
            setStatus("Loading available agents...");
            const response = await fetch("/orchestrate/available-agents");
            if (!response.ok) {
                throw new Error("Failed to load available agents");
            }
            const payload = await response.json();
            const agents = payload.agents || [];
            agentSelect.innerHTML = "";
            for (const agent of agents) {
                const option = document.createElement("option");
                option.value = JSON.stringify(agent);
                option.textContent = `${agent.name} (${agent.kind})`;
                agentSelect.appendChild(option);
            }
            setStatus(agents.length ? `Loaded ${agents.length} published agents.` : "No published agents found.");
        }

        async function dispatchMessage() {
            const agent = selectedAgent();
            const message = messageInput.value.trim();
            if (!agent) {
                setStatus("Select an agent first.");
                return;
            }
            if (!message) {
                setStatus("Enter a message before dispatching.");
                return;
            }

            setStatus("Dispatching message...");
            const response = await fetch("/orchestrate/dispatch-to-agent", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    message,
                    conversation_id: conversationIdInput.value.trim(),
                    parameters: {
                        agent_type: agent.kind,
                        agent_name: agent.name,
                        agent_id: agent.id,
                    },
                }),
            });
            const payload = await response.json();
            if (!response.ok) {
                throw new Error(payload.detail || payload.error || "Dispatch failed");
            }
            conversationIdInput.value = payload.conversation_id || conversationIdInput.value;
            resultOutput.textContent = JSON.stringify(payload, null, 2);
            setStatus(`Message queued for ${agent.name}. Conversation id: ${conversationIdInput.value}`);
        }

        async function getResult() {
            const conversationId = conversationIdInput.value.trim();
            if (!conversationId) {
                setStatus("Dispatch first or enter a conversation id.");
                return;
            }

            setStatus("Loading shared-state result...");
            const response = await fetch(`/orchestrate/dispatch-results/${encodeURIComponent(conversationId)}`);
            const payload = await response.json();
            if (!response.ok) {
                throw new Error(payload.detail || payload.error || "Failed to load result");
            }
            resultOutput.textContent = JSON.stringify(payload, null, 2);
            setStatus(`Loaded result for ${conversationId}.`);
        }

        dispatchButton.addEventListener("click", () => dispatchMessage().catch((error) => setStatus(error.message)));
        resultButton.addEventListener("click", () => getResult().catch((error) => setStatus(error.message)));
        loadAgents().catch((error) => setStatus(error.message));
    </script>
</body>
</html>
"""
