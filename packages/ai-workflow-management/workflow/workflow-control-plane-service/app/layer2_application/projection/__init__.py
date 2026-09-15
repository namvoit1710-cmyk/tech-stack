"""Run-state projection — application API.

``ProjectedRunState`` is the ``evolve`` half of the decide/evolve seam — a rich
value object that folds events into itself via ``ProjectedRunState.apply`` and
lives in ``app.layer1_domain.run_projection``. This package holds the
repo-driven ``rebuild_run_state`` shell and re-exports the state value object so
callers have one cohesive projection entry point.
"""

from app.layer1_domain.run_projection import ProjectedRunState
from app.layer2_application.projection.rebuild import rebuild_run_state

__all__ = ["ProjectedRunState", "rebuild_run_state"]
