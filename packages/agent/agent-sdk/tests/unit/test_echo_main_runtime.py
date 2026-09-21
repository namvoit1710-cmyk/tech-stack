import ast
import pathlib


def test_echo_main_runs_direct_app_without_reload():
    main_path = (
        pathlib.Path(__file__).parent.parent.parent
        / "examples"
        / "echo_agent"
        / "main.py"
    )
    source = main_path.read_text()
    tree = ast.parse(source)

    uvicorn_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "uvicorn"
        and node.func.attr == "run"
    ]
    assert uvicorn_calls, "main.py must call uvicorn.run(...)"

    call = uvicorn_calls[-1]
    assert call.args, "uvicorn.run should receive an app argument"
    app_arg = call.args[0]
    assert isinstance(app_arg, ast.Call)
    assert isinstance(app_arg.func, ast.Name) and app_arg.func.id == "create_app"
    assert not app_arg.args and not app_arg.keywords

    reload_kw = next((kw for kw in call.keywords if kw.arg == "reload"), None)
    assert reload_kw is None, "main.py must not enable reload for manual echo smokes"
