# Aurora — Excel Add-in

Adds an **Analyze Selection** button to the Excel Home ribbon. Select a
range with headers, click the button, and Aurora runs glass-box
quantitative analysis on it — anomalies, regimes, causality, physics
fits, all cited.

The add-in is a **thin client** for the local Aurora Studio. Same
architecture as the VS Code extension: Excel sends the selection to
your running Studio (`localhost:8000`), Studio runs the analysis,
results render in the Excel task pane.

## Files

| File | Purpose |
|---|---|
| `manifest.xml` | Office add-in manifest declaring the ribbon button + task pane |
| `taskpane.js` | Client-side logic: read selection, POST to Aurora, render findings |

The actual task-pane HTML (`/excel-taskpane.html`) is served by Aurora
Studio itself — that route should be added to `studio_api.py` when the
add-in is enabled. Stub it in for now and serve a `<div id="findings">`
container that this `taskpane.js` populates.

## Setup (sideload for development)

1. **Start Aurora Studio** locally:
   ```bash
   python studio_api.py
   ```
2. **Sideload the add-in** in Excel:
   - Excel → Insert → My Add-ins → Manage My Add-ins → Upload My Add-in
   - Select `extensions/excel/manifest.xml`
3. The **Aurora** group appears on the Home ribbon. Click
   **Analyze Selection**.

## Publishing to AppSource (production path)

Microsoft's distribution channel for Office add-ins. You'll need:

1. A Microsoft Partner Center account: <https://partner.microsoft.com/>
2. A signed `manifest.xml` with a real GUID (replace
   `00000000-aaaa-bbbb-cccc-000000000001` in the manifest)
3. A hosted version of the task-pane HTML + JS at a public HTTPS URL
   (so Excel desktop + web can both load it)
4. Submit via Partner Center → Office Store

For self-hosted enterprise deployments, IT can push the manifest via
**Microsoft 365 admin centre → Integrated apps**.

## Roadmap

| Feature | Status |
|---|---|
| Ribbon button + task pane | ✅ scaffolded |
| Send selection to Aurora over HTTP | ✅ scaffolded (depends on `/api/run/inline-csv` being added to Studio) |
| Render findings in task pane | ✅ scaffolded |
| "Highlight anomalous rows" — overlay severity colours in the sheet | v0.2 |
| Write findings back into a new sheet | v0.2 |
| Configuration UI for `studioUrl` + `defaultDepth` | v0.2 |
| MCP integration when running alongside Copilot | v0.3 |

## License

Apache 2.0.
