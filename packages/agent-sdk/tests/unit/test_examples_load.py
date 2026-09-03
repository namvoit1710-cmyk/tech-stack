import ast
import os

EXAMPLES_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "examples")


def test_convenient_tool_agent_parses():
    path = os.path.join(EXAMPLES_DIR, "convenient_tool_agent.py")
    assert os.path.isfile(path), f"Example file not found: {path}"
    with open(path) as f:
        source = f.read()
    ast.parse(source)


def test_convenient_tool_agent_uses_agent_graph_builder():
    path = os.path.join(EXAMPLES_DIR, "convenient_tool_agent.py")
    with open(path) as f:
        source = f.read()
    assert "AgentGraphBuilder" in source


def test_convenient_tool_agent_does_not_use_tool_agent_builder():
    path = os.path.join(EXAMPLES_DIR, "convenient_tool_agent.py")
    with open(path) as f:
        source = f.read()
    assert "ToolAgentBuilder" not in source


def test_convenient_tool_agent_does_not_import_langgraph_directly():
    path = os.path.join(EXAMPLES_DIR, "convenient_tool_agent.py")
    with open(path) as f:
        source = f.read()
    assert "import langgraph" not in source
    assert "from langgraph" not in source


def test_convenient_tool_agent_does_not_import_langchain_directly():
    path = os.path.join(EXAMPLES_DIR, "convenient_tool_agent.py")
    with open(path) as f:
        source = f.read()
    assert "import langchain" not in source
    assert "from langchain" not in source


def test_subgraph_example_parses():
    path = os.path.join(EXAMPLES_DIR, "subgraph_example.py")
    assert os.path.isfile(path), f"Example file not found: {path}"
    with open(path) as f:
        source = f.read()
    ast.parse(source)


def test_subgraph_example_does_not_import_langgraph_directly():
    path = os.path.join(EXAMPLES_DIR, "subgraph_example.py")
    with open(path) as f:
        source = f.read()
    assert "import langgraph" not in source
    assert "from langgraph" not in source
