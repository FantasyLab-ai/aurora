"""
JupyterLab / IPython rendering helpers for Aurora SDK objects.

When you do ``r = aurora.run(df)`` in a notebook and then evaluate
``r`` (or ``r.findings``, ``r.bundle``) as the last expression in a
cell, Jupyter looks for a ``_repr_html_`` method on the object. If
present, the returned HTML is what Jupyter displays — which is much
nicer than ``<aurora_sdk.RunResult object at 0x...>``.

This module defines the HTML templates used by:

  * ``RunResult._repr_html_``  — top-line summary card
  * ``Findings._repr_html_``   — sortable findings table
  * ``Bundle._repr_html_``     — bundle metadata + integrity card

All HTML is self-contained (no external CSS, no JS) so it renders
identically in JupyterLab, classic notebook, VS Code's notebook view,
nbviewer, and the GitHub notebook preview. We use inline styles
scoped via a unique class prefix so it doesn't accidentally inherit
the surrounding theme.

Design rules:

  * No JavaScript. Notebook environments differ wildly in what JS is
    allowed; HTML+CSS is universal.
  * No external image URLs. Self-contained means self-contained.
  * Dark-mode friendly: we use semi-transparent backgrounds + CSS vars
    where possible, with sensible light/dark fallbacks.
  * Truncate long lists in the HTML (cap at 25 rows). Notebook output
    cells can choke on giant tables.
"""
from __future__ import annotations

import html as _html
from typing import Any, Dict, Iterable, List, Optional


# Cap the number of findings rendered in a notebook table. The full
# list is always accessible via the Python API; this is just for
# display. Beyond 25 rows a notebook cell becomes hard to scroll
# without losing context.
MAX_TABLE_ROWS = 25

# Severity → emoji + colour mapping. Inline RGB so it doesn't depend
# on the user's notebook theme.
_SEVERITY_STYLE = {
    "crit": ("🔴", "#ff4f6b"),
    "warn": ("🟡", "#ffb84a"),
    "info": ("🔵", "#5aa9ff"),
    "ok":   ("🟢", "#3ed080"),
}


def render_runresult_html(run_result: Any) -> str:
    """Top-line summary card for a RunResult. Shows run_id, dataset
    name + size, severity rollup, methods used, fabricated count, and
    bundle hash. Compact enough to fit a notebook cell at default zoom."""
    bundle = getattr(run_result, "bundle", None)
    if bundle is None:
        return "<i>Aurora RunResult (incomplete)</i>"

    doc = getattr(bundle, "doc", {}) or {}
    summary = _runresult_summary(run_result, doc)
    severity_counts = summary["severity_counts"]
    severity_pills = _render_severity_pills(severity_counts)
    methods = ", ".join(summary["methods_used"][:5])
    if len(summary["methods_used"]) > 5:
        methods += f" <em>+{len(summary['methods_used']) - 5} more</em>"

    fabricated_badge = (
        "<span style='color:#3ed080; font-weight:600'>✓ 0 fabricated</span>"
        if summary["fabricated"] == 0 else
        f"<span style='color:#ff4f6b; font-weight:600'>⚠ {summary['fabricated']} fabricated</span>"
    )

    confidence_pct = (
        f"{summary['confidence']*100:.0f}%"
        if summary["confidence"] is not None else "—"
    )

    return f"""
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
            border:1px solid rgba(128,128,128,0.3); border-radius:8px;
            padding:14px 18px; margin:8px 0; max-width:780px;
            background:rgba(120,120,180,0.05);">
  <div style="font-weight:600; font-size:1.05em; margin-bottom:6px;">
    Aurora Run · <code>{_html.escape(summary["run_id"])}</code>
  </div>
  <div style="color:rgba(128,128,128,0.95); font-size:0.9em; margin-bottom:10px;">
    Dataset: <code>{_html.escape(summary["dataset_name"])}</code>
    · {summary["rows"]} rows × {summary["cols"]} cols
    · SHA-256 <code>{_html.escape(summary["dataset_sha"][:16])}…</code>
  </div>
  <table style="border-collapse:collapse; width:100%; font-size:0.92em;">
    <tr><td style="padding:3px 8px 3px 0; color:rgba(128,128,128,0.95)">
        Findings ({summary["finding_count"]})</td>
        <td>{severity_pills}</td></tr>
    <tr><td style="padding:3px 8px 3px 0; color:rgba(128,128,128,0.95)">
        Confidence</td>
        <td>{confidence_pct}</td></tr>
    <tr><td style="padding:3px 8px 3px 0; color:rgba(128,128,128,0.95)">
        Audit</td>
        <td>{fabricated_badge}
            · bundle <code>{_html.escape(summary["bundle_hash"][:16])}…</code></td></tr>
    <tr><td style="padding:3px 8px 3px 0; color:rgba(128,128,128,0.95)">
        Methods</td>
        <td style="font-size:0.88em;">{methods}</td></tr>
  </table>
  <div style="color:rgba(128,128,128,0.75); font-size:0.78em; margin-top:8px;">
    Use <code>r.findings</code> for the table, <code>r.bundle</code> for the artifact,
    <code>r.bundle.save("...")</code> to ship.
  </div>
</div>
""".strip()


def render_findings_html(findings: Iterable[Dict[str, Any]],
                          *, title: Optional[str] = None) -> str:
    """Sortable-looking table of findings. Pure HTML (no JS) so it
    works in every notebook environment.

    Caps the rendered rows at ``MAX_TABLE_ROWS`` with a "+N more"
    footer when truncated. Users wanting the full list can still
    iterate the underlying object."""
    finding_list = list(findings)
    n = len(finding_list)
    if n == 0:
        return (
            "<div style='font-family:-apple-system,sans-serif; "
            "padding:10px; color:rgba(128,128,128,0.85)'>"
            "<i>Findings: 0 — nothing flagged.</i></div>"
        )

    display = finding_list[:MAX_TABLE_ROWS]
    truncated = n - len(display)

    rows = []
    for f in display:
        sev = str(f.get("severity", "info")).lower()
        emoji, colour = _SEVERITY_STYLE.get(sev, ("•", "#888"))
        conf = f.get("confidence")
        conf_str = f"{conf*100:.0f}%" if isinstance(conf, (int, float)) else "—"
        title_str = _html.escape(str(f.get("title", "")))[:120]
        method_str = _html.escape(str(f.get("method", "")))
        rows.append(
            f"<tr>"
            f"<td style='padding:5px 8px; color:{colour}; font-weight:600;'>{emoji} {sev}</td>"
            f"<td style='padding:5px 8px; font-size:0.88em; opacity:0.8;'>{conf_str}</td>"
            f"<td style='padding:5px 8px;'>{title_str}</td>"
            f"<td style='padding:5px 8px; font-family:monospace; font-size:0.85em; opacity:0.7;'>{method_str}</td>"
            f"</tr>"
        )

    title_html = (
        f"<div style='font-weight:600; margin-bottom:6px;'>{_html.escape(title)}</div>"
        if title else ""
    )

    trunc_footer = (
        f"<div style='color:rgba(128,128,128,0.7); font-size:0.85em; "
        f"padding:6px 8px;'>… plus {truncated} more "
        f"(use the Python object for the full list)</div>"
        if truncated else ""
    )

    return f"""
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
            border:1px solid rgba(128,128,128,0.3); border-radius:8px;
            margin:8px 0; max-width:920px;
            background:rgba(120,120,180,0.03); overflow:hidden;">
  {title_html}
  <table style="border-collapse:collapse; width:100%; font-size:0.92em;">
    <thead>
      <tr style="background:rgba(128,128,128,0.1);
                  border-bottom:1px solid rgba(128,128,128,0.3);">
        <th style="text-align:left; padding:6px 8px; font-weight:600;">Sev</th>
        <th style="text-align:left; padding:6px 8px; font-weight:600;">Conf</th>
        <th style="text-align:left; padding:6px 8px; font-weight:600;">Title</th>
        <th style="text-align:left; padding:6px 8px; font-weight:600;">Method</th>
      </tr>
    </thead>
    <tbody>
      {"".join(rows)}
    </tbody>
  </table>
  {trunc_footer}
</div>
""".strip()


def render_bundle_html(bundle: Any) -> str:
    """Bundle metadata + integrity card. Includes the hash, run id,
    dataset SHA, fabricated count, and a verify/sign hint."""
    doc = getattr(bundle, "doc", {}) or {}
    integrity = doc.get("integrity", {}) or {}
    dataset = doc.get("dataset", {}) or {}
    run = doc.get("run", {}) or {}
    findings_list = doc.get("findings") or []

    sha = str(integrity.get("content_hash", ""))
    signed = bool((integrity.get("signature") or {}).get("public_key_b64"))
    fabricated = getattr(bundle, "fabricated_count", None)
    if fabricated is None:
        # Best-effort: count findings flagged as fabricated.
        fabricated = sum(1 for f in findings_list if bool(f.get("fabricated", False)))

    return f"""
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
            border:1px solid rgba(128,128,128,0.3); border-radius:8px;
            padding:14px 18px; margin:8px 0; max-width:780px;
            background:rgba(120,120,180,0.05);">
  <div style="font-weight:600; font-size:1.05em; margin-bottom:8px;">
    📦 Aurora Bundle · v{_html.escape(str(doc.get("bundle_version", "?")))}
  </div>
  <table style="border-collapse:collapse; width:100%; font-size:0.92em;">
    <tr><td style="padding:3px 8px 3px 0; color:rgba(128,128,128,0.95)">
        Run ID</td>
        <td><code>{_html.escape(str(run.get("run_id", "—")))}</code></td></tr>
    <tr><td style="padding:3px 8px 3px 0; color:rgba(128,128,128,0.95)">
        Dataset</td>
        <td><code>{_html.escape(str(dataset.get("basename", "—")))}</code>
            · {dataset.get("rows", "?")} rows × {dataset.get("cols", "?")} cols</td></tr>
    <tr><td style="padding:3px 8px 3px 0; color:rgba(128,128,128,0.95)">
        Findings</td>
        <td>{len(findings_list)}</td></tr>
    <tr><td style="padding:3px 8px 3px 0; color:rgba(128,128,128,0.95)">
        Integrity</td>
        <td><code>SHA-256 {_html.escape(sha[:16])}…</code>
            {"· <span style='color:#3ed080'>✓ signed (Ed25519)</span>" if signed else ""}</td></tr>
    <tr><td style="padding:3px 8px 3px 0; color:rgba(128,128,128,0.95)">
        Fabricated</td>
        <td>{"<span style='color:#3ed080; font-weight:600'>✓ 0</span>"
              if fabricated == 0 else
              f"<span style='color:#ff4f6b; font-weight:600'>⚠ {fabricated}</span>"}</td></tr>
  </table>
  <div style="color:rgba(128,128,128,0.75); font-size:0.78em; margin-top:8px;">
    <code>bundle.save("audit.aurora.json")</code> to ship ·
    <code>bundle.verify()</code> to check integrity ·
    <code>bundle.sign(key)</code> for Ed25519 signature.
  </div>
</div>
""".strip()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _runresult_summary(run_result: Any, doc: Dict[str, Any]) -> Dict[str, Any]:
    """Pull the fields we want to render into one flat dict.

    Defensive: every lookup has a fallback so a partial bundle doesn't
    crash the renderer."""
    dataset = doc.get("dataset", {}) or {}
    run = doc.get("run", {}) or {}
    integrity = doc.get("integrity", {}) or {}
    findings_list = doc.get("findings") or []

    severity_counts: Dict[str, int] = {}
    methods: List[str] = []
    seen_methods = set()
    for f in findings_list:
        sev = str(f.get("severity", "info")).lower()
        severity_counts[sev] = severity_counts.get(sev, 0) + 1
        m = f.get("method")
        if m and m not in seen_methods:
            methods.append(str(m))
            seen_methods.add(str(m))

    return {
        "run_id": run.get("run_id", "—"),
        "dataset_name": dataset.get("basename", "—"),
        "dataset_sha": dataset.get("sha256", ""),
        "rows": dataset.get("rows", "?"),
        "cols": dataset.get("cols", "?"),
        "severity_counts": severity_counts,
        "methods_used": methods,
        "finding_count": len(findings_list),
        "confidence": getattr(run_result, "confidence", None),
        "fabricated": getattr(run_result, "fabricated_count", 0) or 0,
        "bundle_hash": integrity.get("content_hash", ""),
    }


def _render_severity_pills(severity_counts: Dict[str, int]) -> str:
    """Render the severity rollup as colour pills."""
    if not severity_counts:
        return "<i style='opacity:0.6'>none</i>"
    bits: List[str] = []
    for sev in ("crit", "warn", "info", "ok"):
        n = severity_counts.get(sev, 0)
        if n <= 0:
            continue
        _, colour = _SEVERITY_STYLE.get(sev, ("•", "#888"))
        bits.append(
            f"<span style='display:inline-block; padding:1px 8px; margin-right:4px; "
            f"border-radius:10px; background:{colour}; color:white; "
            f"font-size:0.85em; font-weight:600;'>{sev} {n}</span>"
        )
    return "".join(bits) if bits else "<i style='opacity:0.6'>none</i>"
