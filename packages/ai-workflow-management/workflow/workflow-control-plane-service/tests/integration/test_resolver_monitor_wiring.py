"""The shadow counter is only real if bootstrap actually wires the monitor.

``TraversalResolverStrategy`` is where an expression can read a file_ref's
REFERENCE metadata instead of its data (``{{$node.version}}`` returning the
envelope's version rather than failing). ``variable_resolve_envelope_bare_read``
counts that, and the count is how the decision to refuse it gets taken on
tenant-1 numbers rather than on reasoning.

Built without a monitor the counter can never fire, and the shadow would read a
reassuring zero forever — a measurement that was never taken, reported as
evidence. Hence a wiring test rather than trust.

Lives in ``tests/integration`` because it builds the whole container (~15s);
the behaviour itself is unit-tested in
``tests/unit/test_file_ref_missing_key_diagnostics.py``.
"""

from app.layer2_application.resolve.variable_resolver import (
    TraversalResolverStrategy,
)
from bootstrap import build_app_container


def test_the_production_traversal_strategy_has_a_monitor():
    container = build_app_container()
    resolver = container["resolve_variable_paths"]._resolver

    traversal = [
        s for s in resolver._strategies
        if isinstance(s, TraversalResolverStrategy)
    ]
    assert traversal, "no TraversalResolverStrategy in the production chain"
    assert traversal[0]._monitor is not None, (
        "TraversalResolverStrategy built without a monitor - "
        "variable_resolve_envelope_bare_read can never fire in production"
    )
