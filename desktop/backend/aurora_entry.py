"""
Aurora backend — frozen entry dispatcher.

This is the single entry point PyInstaller freezes into the bundled
backend executable (``aurora-backend[.exe]``). Aurora's run pipeline is
a two-hop subprocess chain:

    studio_api  --subprocess-->  run_aurora_with_steps_1_4
                --subprocess-->  run_aurora_dataset_runner  (runs the
                                 analytical pipeline in-process)

In a normal venv each hop is ``python -m scripts.<module>``. That does
NOT work inside a PyInstaller bundle: there is no ``python`` and ``-m``
isn't honored. The standard fix is for the frozen executable to
re-invoke ITSELF with a sentinel as ``argv[1]`` that selects which of
the three roles to play. The frozen ``studio_api`` and
``run_aurora_with_steps_1_4`` build their subprocess commands using these
sentinels (gated on ``sys.frozen`` — see the frozen-awareness blocks in
those files). In dev mode none of this runs; the normal ``-m`` path is
used and this file is never imported.

Roles, by ``argv[1]``:

    __aurora_runner_steps__     -> scripts.run_aurora_with_steps_1_4.main
    __aurora_runner_dataset__   -> scripts.run_aurora_dataset_runner.main
    (anything else / none)      -> studio_api.main   (the Flask server)

The sentinel is stripped before the target's argparse sees argv.
"""
import os
import sys


SENTINEL_STEPS   = "__aurora_runner_steps__"
SENTINEL_DATASET = "__aurora_runner_dataset__"


def _ensure_importable() -> None:
    """Make ``studio_api`` and the ``scripts`` package importable.

    When frozen, PyInstaller mounts everything under ``sys._MEIPASS`` and
    the spec's ``pathex`` puts the repo root on ``sys.path`` already, so
    this is mostly a no-op belt-and-braces. When run as a plain script
    (e.g. ``python desktop/backend/aurora_entry.py`` during testing) it
    adds the repo root so imports resolve.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.abspath(os.path.join(here, "..", ".."))
    meipass = getattr(sys, "_MEIPASS", None)
    for p in (meipass, repo_root):
        if p and p not in sys.path:
            sys.path.insert(0, p)


def main() -> int:
    _ensure_importable()
    argv = sys.argv

    if len(argv) > 1 and argv[1] == SENTINEL_STEPS:
        # Hand the remaining argv (minus the sentinel) to the steps runner.
        sys.argv = [argv[0]] + argv[2:]
        from scripts.run_aurora_with_steps_1_4 import main as steps_main
        return int(steps_main() or 0)

    if len(argv) > 1 and argv[1] == SENTINEL_DATASET:
        sys.argv = [argv[0]] + argv[2:]
        # NOTE: the dataset runner's entry is named ``_main`` (its
        # __main__ block calls _main()), NOT ``main``. The steps runner
        # above does use ``main``. Don't "fix" this to match.
        from scripts.run_aurora_dataset_runner import _main as dataset_main
        return int(dataset_main() or 0)

    # Default role: the Flask server. studio_api.main() blocks (app.run).
    from studio_api import main as server_main
    server_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
