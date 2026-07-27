"""Compatibility alias — the canonical runner moved INTO the package
(``fantasyai.aurora.scripts.run_aurora_dataset_runner``) so that
``pip install aurora-mcp`` ships the full analyze path. This module
aliases itself to the packaged one, so every existing import path,
attribute (underscore helpers included), and ``python -m
scripts.run_aurora_dataset_runner`` invocation behaves identically."""
import sys

from fantasyai.aurora.scripts import run_aurora_dataset_runner as _real

sys.modules[__name__] = _real

if __name__ == "__main__":
    _real._main()
