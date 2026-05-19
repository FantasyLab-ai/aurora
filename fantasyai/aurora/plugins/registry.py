"""
Plugin discovery + safe invocation.

Plugins register via the ``aurora_plugins`` entry-point group. We
walk the group via ``importlib.metadata.entry_points`` and produce a
``List[PluginInfo]`` — name, dotted target, status (loaded /
import_error / contract_error), version (when the parent package is
metadata-aware), and load-time error if any.

``run_plugin_methods`` invokes every loaded plugin in turn, with a
defensive try/except so a buggy plugin never takes down the run. The
returned dict mirrors ``extended_runner``'s shape so the rest of the
pipeline (state_builder, findings rendering) treats plugins
identically to first-party methods.
"""
from __future__ import annotations

import importlib
import logging
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, List, Optional

LOG = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "aurora_plugins"


class PluginValidationError(ValueError):
    """Raised when a plugin's finding doesn't match the contract."""


@dataclass
class PluginInfo:
    """Public-facing metadata about an installed plugin."""
    name:         str
    target:       str           # dotted path "pkg.mod:fn"
    status:       str           # "loaded" | "import_error" | "contract_error" | "disabled"
    package:      Optional[str] = None
    version:      Optional[str] = None
    error:        Optional[str] = None
    # Last-run telemetry.
    last_status:  Optional[str] = None   # "fit" | "skipped" | "failed"
    last_elapsed_s: Optional[float] = None
    last_error:   Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ----------------------------------------------------------------------
# Discovery
# ----------------------------------------------------------------------

def _iter_entry_points() -> List[Any]:
    """Return entry points for our group, tolerating old/new APIs."""
    try:
        from importlib.metadata import entry_points
    except Exception:
        return []
    try:
        eps = entry_points()
        # Python 3.10+: EntryPoints with select(); 3.9-: dict-like.
        if hasattr(eps, "select"):
            return list(eps.select(group=ENTRY_POINT_GROUP))
        return list(eps.get(ENTRY_POINT_GROUP, []))
    except Exception:
        return []


def discover_plugins() -> List[PluginInfo]:
    """Walk ``aurora_plugins`` entry points + return a status list."""
    infos: List[PluginInfo] = []
    for ep in _iter_entry_points():
        name = getattr(ep, "name", "<?>")
        target = getattr(ep, "value", None) or _format_target(ep)
        try:
            obj = ep.load()
        except Exception as e:
            infos.append(PluginInfo(
                name=name,
                target=target or "?",
                status="import_error",
                error=f"{type(e).__name__}: {e}",
            ))
            continue
        if not callable(obj):
            infos.append(PluginInfo(
                name=name, target=target or "?",
                status="contract_error",
                error="entry point is not callable",
            ))
            continue
        # Try to pull distribution metadata.
        version = None
        package = None
        try:
            dist = getattr(ep, "dist", None)
            if dist is not None:
                package = getattr(dist, "name", None) or getattr(dist, "project_name", None)
                version = getattr(dist, "version", None)
        except Exception:
            pass
        infos.append(PluginInfo(
            name=name,
            target=target or "?",
            status="loaded",
            package=package,
            version=version,
        ))
    return infos


def _format_target(ep: Any) -> str:
    """Best-effort 'module:attr' formatter for older EntryPoint shapes."""
    try:
        mod = getattr(ep, "module", None)
        attr = getattr(ep, "attr", None) or getattr(ep, "name", None)
        if mod and attr:
            return f"{mod}:{attr}"
    except Exception:
        pass
    return str(ep)


# ----------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------

_REQUIRED_FINDING_FIELDS = ("method", "title", "severity", "evidence")
_ALLOWED_STATUSES = {"fit", "skipped", "failed"}


def validate_finding(finding: Any, *, method_name: str) -> None:
    """Raise PluginValidationError if the finding violates the contract."""
    if not isinstance(finding, dict):
        raise PluginValidationError(
            f"plugin {method_name!r} returned non-dict ({type(finding).__name__})"
        )
    for k in _REQUIRED_FINDING_FIELDS:
        if k not in finding:
            raise PluginValidationError(
                f"plugin {method_name!r} finding missing {k!r}"
            )
    ev = finding.get("evidence")
    if not isinstance(ev, dict):
        raise PluginValidationError(
            f"plugin {method_name!r} evidence must be a dict"
        )
    status = str(ev.get("status") or "").lower()
    if status not in _ALLOWED_STATUSES:
        raise PluginValidationError(
            f"plugin {method_name!r} evidence.status must be one of "
            f"{_ALLOWED_STATUSES}; got {status!r}"
        )
    # fabricated=True is forbidden for plugins (no fake findings allowed
    # through the glass-box gate). Default False is fine.
    if finding.get("fabricated") is True:
        raise PluginValidationError(
            f"plugin {method_name!r} attempted to emit fabricated=True; refused"
        )


# ----------------------------------------------------------------------
# Invocation
# ----------------------------------------------------------------------

def run_plugin_methods(
    df: Any,
    *,
    target_col: Optional[str] = None,
    time_col: Optional[str] = None,
    timeout_s: float = 60.0,
    enabled_only: bool = True,
) -> Dict[str, Any]:
    """Run every discovered plugin against ``df``.

    Returns the same shape as ``run_extended_methods``::

        {
            "findings":      [...],
            "per_plugin":    {"name": {"status": ..., "elapsed_s": ..., ...}},
            "n_plugins_run": int,
            "n_findings":    int,
            "plugins":       [PluginInfo.to_dict() for each discovered plugin],
        }
    """
    plugins = discover_plugins()
    findings: List[Dict[str, Any]] = []
    per_plugin: Dict[str, Dict[str, Any]] = {}
    n_ran = 0

    for info in plugins:
        if info.status != "loaded":
            per_plugin[info.name] = {
                "status": info.status,
                "error":  info.error,
            }
            continue
        if enabled_only and info.status != "loaded":
            continue
        # Load the callable again — discover_plugins() stored only info.
        fn = _load_callable(info.target)
        if fn is None:
            info.status = "import_error"
            info.error = "lazy reload failed"
            per_plugin[info.name] = {"status": "import_error", "error": info.error}
            continue
        t0 = time.perf_counter()
        try:
            kwargs: Dict[str, Any] = {}
            if target_col is not None:
                kwargs["target_col"] = target_col
            if time_col is not None:
                kwargs["time_col"] = time_col
            result = fn(df, **kwargs)
            elapsed = time.perf_counter() - t0
            # Plugins may emit a single finding OR a list of findings.
            if isinstance(result, list):
                emitted = result
            else:
                emitted = [result]
            n_valid = 0
            errors: List[str] = []
            for f in emitted:
                try:
                    validate_finding(f, method_name=info.name)
                except PluginValidationError as ve:
                    errors.append(str(ve))
                    continue
                # Ensure the method field carries the plugin name so the
                # state_builder finding render can tag it.
                if not str(f.get("method") or "").strip():
                    f["method"] = info.name
                # Plugins emit fabricated=False by default (we already
                # rejected =True above).
                f.setdefault("fabricated", False)
                findings.append(f)
                n_valid += 1
            n_ran += 1
            info.last_status = "fit" if n_valid > 0 else "skipped"
            info.last_elapsed_s = round(elapsed, 3)
            info.last_error = "; ".join(errors) if errors else None
            per_plugin[info.name] = {
                "status":     info.last_status,
                "elapsed_s":  info.last_elapsed_s,
                "n_findings": n_valid,
                "errors":     errors,
            }
        except Exception as e:
            elapsed = time.perf_counter() - t0
            LOG.exception(f"plugin {info.name!r} raised")
            info.last_status = "failed"
            info.last_elapsed_s = round(elapsed, 3)
            info.last_error = f"{type(e).__name__}: {e}"
            per_plugin[info.name] = {
                "status":    "failed",
                "elapsed_s": info.last_elapsed_s,
                "error":     info.last_error,
            }
            # Emit a synthetic "plugin crashed" finding so the user sees
            # the failure honestly rather than silently missing output.
            findings.append({
                "method":     info.name,
                "title":      f"plugin {info.name!r} crashed",
                "description": info.last_error,
                "severity":   "info",
                "confidence": 0.0,
                "fabricated": False,
                "kind_hint":  "plugin_failed",
                "evidence":   {"status": "failed", "error": info.last_error},
            })
    return {
        "findings":      findings,
        "per_plugin":    per_plugin,
        "n_plugins_run": n_ran,
        "n_findings":    len(findings),
        "plugins":       [p.to_dict() for p in plugins],
    }


def _load_callable(target: str) -> Optional[Callable[..., Any]]:
    """Resolve a dotted "pkg.mod:fn" string into a callable."""
    if not target or ":" not in target:
        return None
    mod_name, attr = target.split(":", 1)
    try:
        mod = importlib.import_module(mod_name.strip())
    except Exception:
        return None
    fn = getattr(mod, attr.strip(), None)
    return fn if callable(fn) else None
