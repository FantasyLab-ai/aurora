# Aurora Fire Overlay

OBS browser source that renders an on-brand "Aurora fired" card when
a Decision Contract trips. Reads events from the relay's SSE stream.

## Add to OBS

1. Start the relay: `python -m demos.relay.app` (default port 7077).
2. In OBS: **Source → Add → Browser**.
3. URL: `http://127.0.0.1:7077/overlay/`
4. Width 1920, Height 1080.
5. Check ✅ **Refresh browser when scene becomes active**.
6. Check ✅ **Shutdown source when not visible** (saves CPU between takes).

## URL params (append to the OBS URL)

- `?corner=tr` — where the card lives (`tr` / `tl` / `br` / `bl`). Default `tr`.
- `?hold=8000` — milliseconds to hold the card. Default 8 s; crit holds 10 s.
- `?sound=0` — silence the audio cue (default `1`).
- `?demo=1` — render a static crit card with example data so you can
  frame the overlay in OBS before the relay is running.
- `?relay=https://other-host:7077` — point at a relay on a different
  machine (default: same origin as the page).

## Audio cue

The overlay loads `fire.wav` from this directory. Drop your custom
cue here as `fire.wav` (44.1 kHz, mono, < 1 s). If the file's
missing, the audio element simply silently fails — no breakage.

If you'd rather keep it silent across all videos, add `?sound=0`.

## Brand checklist

Every video should show:

- The `⚡ AURORA` chip top-left of the card (identity).
- The severity chip in the matching colour (crit = magenta, warn = amber, info = cyan).
- The method + field + value + |z|σ + row as the visual climax.
- `0 FABRICATED` chip in the footer (contractual signal — always present).
- The bundle run id, so "verify it yourself" is a real claim.
