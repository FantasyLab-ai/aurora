"""
Aurora Plugin SDK (Q3 Stream C) — third-party method registration.

Aurora's analytical surface is large (24+ methods) but it's not
infinite. Domain specialists want to add methods Aurora doesn't ship:
a specific change-point detector, a domain-specific physics model,
an in-house anomaly scorer. The Plugin SDK makes that possible
*without* forking Aurora.

A plugin is a Python package that, when installed, registers one or
more "method runners" via the standard ``aurora_plugins`` entry-point
group. Each runner receives a pandas DataFrame and returns an
Aurora-shaped finding (the same shape extended_methods uses). Aurora
discovers + invokes plugins through this module's ``discover_plugins``
and ``run_plugin_methods`` functions.

Glass-box compliance: plugins MUST emit findings that pass the same
contract every built-in method does — ``fabricated=False``,
``method`` field set, ``evidence.status`` in {"fit","skipped","failed"}.
The registry validates each finding before propagating it to the
state. A non-compliant plugin gets dropped with a clear error log;
it never silently corrupts results.

A simple plugin looks like:

    # mypkg/aurora_plugin.py
    def my_method(df, **kwargs):
        return {
            "method": "my_method",
            "title": "My method ran",
            "severity": "info",
            "confidence": 0.5,
            "fabricated": False,
            "evidence": {"status": "fit", "n_obs": len(df)},
        }

    # pyproject.toml
    [project.entry-points."aurora_plugins"]
    my_method = "mypkg.aurora_plugin:my_method"

After ``pip install mypkg``, Aurora picks it up automatically and the
method appears in the runner's output alongside built-ins. The
Studio's PLUGINS panel surfaces installed plugins with health status.

Phase 1 of the SDK (this module) ships:
    * Discovery via ``importlib.metadata`` entry points
    * Validation of plugin output against the finding contract
    * Safe invocation with per-plugin timeout + crash-to-skip handling
    * Frontend visibility through /api/plugins
"""
from __future__ import annotations

from .registry import (
    PluginInfo,
    PluginValidationError,
    discover_plugins,
    run_plugin_methods,
    validate_finding,
)

__all__ = [
    "PluginInfo",
    "PluginValidationError",
    "discover_plugins",
    "run_plugin_methods",
    "validate_finding",
]
