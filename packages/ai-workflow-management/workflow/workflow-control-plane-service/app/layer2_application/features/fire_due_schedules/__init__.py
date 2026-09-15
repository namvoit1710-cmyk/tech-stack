"""Schedule (crontab) trigger scheduler feature.

Intentionally does NOT re-export ``FireDueSchedulesUseCase`` from this package
``__init__``. The auto-discovery loader (``features/__init__.py::load_features``)
only registers a feature when its package exposes ``<Name>UseCase``; by omitting
the export we keep this use case OUT of the smart-DI pool and register it
manually in ``bootstrap.py`` only when ``WORKFLOW_SCHEDULER_ENABLED`` is set.
This preserves the env gate (auto-discovery would otherwise wire it
unconditionally) and avoids a spurious "missing dependencies: {'start_run'}"
warning, since ``start_run`` is a use case, not a pool dependency.
"""
