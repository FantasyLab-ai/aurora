# Aurora Plugin SDK

Aurora's analytical surface is large but not infinite. The Plugin
SDK lets you add domain-specific methods that flow through the
pipeline alongside Aurora's built-ins — same finding shape, same
glass-box contract, same Studio rendering — without forking the
project.

This is **Q3 Stream C** of the 12-month roadmap. The SDK is
intentionally minimal in this first cut: discovery + safe invocation +
validation. Future versions will add plugin-specific config schemas,
versioned method contracts, and the marketplace listing flow.

## What a plugin is

A plugin is a Python function that takes a pandas DataFrame and
returns one Aurora-shaped finding (or a list of them). It registers
through the standard `aurora_plugins` entry-point group.

Minimum viable plugin:

```python
# mypkg/aurora_plugin.py

def my_method(df, **kwargs):
    """Return a single Aurora-shaped finding."""
    return {
        "method": "my_method",
        "title":  f"my_method ran on {len(df)} rows",
        "severity": "info",
        "confidence": 0.5,
        "fabricated": False,                # MANDATORY — must be False
        "evidence": {
            "status": "fit",                # fit | skipped | failed
            "n_obs":  int(len(df)),
            # ... any other fields your method computed
        },
    }
```

`pyproject.toml`:

```toml
[project]
name = "aurora-plugin-mymethod"
version = "0.1.0"

[project.entry-points."aurora_plugins"]
my_method = "mypkg.aurora_plugin:my_method"
```

After `pip install -e .`, Aurora picks it up automatically. The
Studio's **PLUGINS** chip in the top toolbar shows the count of
loaded plugins; click for a per-plugin status list (load state,
version, last-run telemetry).

## The contract

Every plugin finding MUST include:

| Field | Type | Required | Notes |
|---|---|---|---|
| `method` | str | yes | Defaults to the entry-point name if you don't set it |
| `title` | str | yes | One-line headline |
| `severity` | str | yes | One of: `crit`, `warn`, `info`, `ok` |
| `evidence.status` | str | yes | One of: `fit`, `skipped`, `failed` |
| `fabricated` | bool | no | Defaults to `False`. **`True` is refused.** |
| `confidence` | float | no | 0..1; defaults to omitted |
| `description` | str | no | Multi-line body |
| `evidence` | dict | yes | Method-specific structured payload |

Findings missing required fields, or with `evidence.status` outside
the allowed set, get dropped at the validation gate. The Studio's
PLUGINS panel surfaces the validation error so you can fix the
plugin without trawling logs.

`fabricated=True` is hard-refused. Aurora's contract is that every
finding has provenance to a named method; allowing plugins to claim
otherwise would break the glass-box guarantee.

## Plugin lifecycle

1. **Discovery** — Aurora calls `discover_plugins()` once per process
   (cached). Reads the `aurora_plugins` entry-point group; resolves
   each entry to a callable.

2. **Invocation** — for every run, `run_plugin_methods(df, ...)`
   walks the loaded plugins and calls each with the analysis frame.

3. **Validation** — each emitted finding is checked against the
   contract. Valid ones flow into the bundle; invalid ones are
   dropped with the error recorded.

4. **Failure isolation** — if a plugin raises, the runner catches
   the exception and emits a synthetic `<plugin> crashed` finding
   with `evidence.status=failed`. The run still completes; other
   plugins still run.

5. **Telemetry** — `PluginInfo.last_status` / `.last_elapsed_s` /
   `.last_error` track the most recent run. The PLUGINS panel
   surfaces them per-plugin.

## Surface guarantees

- A plugin **cannot** modify the bundle outside its own finding(s).
- A plugin **cannot** access the user's KB / contracts / other run
  state — it only sees the DataFrame passed to its function.
- A plugin **cannot** suppress other findings.
- A plugin **may** import any Python package its `pyproject.toml`
  declares — Aurora doesn't sandbox imports.

## Authoring tips

- **Use the same finding shape as built-ins.** Look at
  `fantasyai/aurora/math/methods/var.py` for a complete example.
- **Emit a "skipped" finding when your method doesn't apply** rather
  than silently returning nothing. Aurora's contract is honesty.
- **Keep your plugin fast.** Plugins run on every analysis; a slow
  plugin slows every run.
- **Pin your dependencies.** Aurora doesn't pin scipy / pandas
  versions for plugins; if your plugin needs a specific version,
  declare it in your own `pyproject.toml`.

## Reference API

```python
from fantasyai.aurora.plugins import (
    discover_plugins,        # → List[PluginInfo]
    run_plugin_methods,      # → {findings, per_plugin, plugins, ...}
    validate_finding,        # raises PluginValidationError on contract violation
    PluginInfo,
    PluginValidationError,
)
```

## Frontend hooks

- **PLUGINS chip** in the top toolbar — shows loaded count + opens
  the panel with per-plugin status.
- **Finding cards** for plugin output render with the same severity
  badges, method badge, and trust row as built-in methods.

## What's next

Future SDK versions will add:

- **Versioned contract** — plugins declare which Aurora API version
  they target so Aurora can refuse stale plugins on upgrades.
- **Per-plugin config schema** — plugin authors declare a JSON
  Schema for their config; Aurora renders it in the PLUGINS panel
  as a real form.
- **Marketplace listing** — once domain packs ship as installable
  units, plugins become a parallel distribution channel.

## See also

- [docs/methods.md](methods.md) — Aurora's built-in method catalogue
- [docs/new-methods.md](new-methods.md) — the 7 v1.2 extended methods
  whose runner the Plugin SDK piggybacks on
