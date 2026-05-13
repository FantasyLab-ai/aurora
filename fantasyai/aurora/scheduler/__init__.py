"""
Overnight learning scheduler.

A separate process from studio_api that fetches data from trusted public
sources, runs Aurora's full analysis pipeline on each, and contributes
findings to the prior library.

Public API:

    from fantasyai.aurora.scheduler import (
        SchedulerConfig, load_config,
        run_overnight_pass,
        catch_up_check, should_fire_now,
        list_supported_sources,
    )

CLI entry: ``python -m fantasyai.aurora.scheduler [--once|--watch|--run-now]``
"""
from __future__ import annotations

from .overnight import (
    SchedulerConfig,
    load_config,
    save_config,
    run_overnight_pass,
    fetch_and_run_one_source,
    catch_up_check,
    should_fire_now,
    list_supported_sources,
    SCHEDULER_STATE_FILE,
    DEFAULT_SCHEDULE_PATH,
)
from .changelog import (
    ValidationEvent,
    update_priors_from_run,
    promote_candidates_if_ready,
    deprecate_if_contradicted,
    write_draft_changelog,
    run_validation_loop,
    list_priors,
    library_summary,
    DEFAULT_PRIORS_ROOT,
    DEFAULT_CHANGELOG_ROOT,
    TRUSTED_SOURCES,
)

__all__ = [
    "SchedulerConfig",
    "load_config", "save_config",
    "run_overnight_pass",
    "fetch_and_run_one_source",
    "catch_up_check", "should_fire_now",
    "list_supported_sources",
    "SCHEDULER_STATE_FILE", "DEFAULT_SCHEDULE_PATH",
    "ValidationEvent",
    "update_priors_from_run",
    "promote_candidates_if_ready",
    "deprecate_if_contradicted",
    "write_draft_changelog",
    "run_validation_loop",
    "list_priors", "library_summary",
    "DEFAULT_PRIORS_ROOT", "DEFAULT_CHANGELOG_ROOT", "TRUSTED_SOURCES",
]
