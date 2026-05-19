# Aurora — Google Sheets Add-on

Companion to the Excel add-in. Adds an **Aurora** menu to Google
Sheets. The user highlights a range, opens the sidebar, clicks
**Analyze Selection**, and Aurora returns cited findings.

## Architecture caveat (read first)

Google Apps Script runs on Google's servers. To call your locally-
running Aurora Studio from Apps Script, you need one of:

1. **An ngrok tunnel** to expose your local Studio publicly
2. **Aurora hosted on a public URL** (your own server / Cloud Run)
3. **Skip Sheets, use Excel** — Excel add-ins run client-side and CAN
   talk to localhost directly. The Sheets add-on is for the "hosted
   Aurora" path, not the "local-first laptop" path.

If you're 100% local-first, the VS Code + Excel + Jupyter surfaces fit
better. The Sheets add-on exists for teams that already host Aurora
centrally.

## Files

| File | Purpose |
|---|---|
| `Code.gs` | Apps Script server code: menu wiring, range reading, HTTP to Aurora |
| `Sidebar.html` | Sidebar UI shown when "Open Aurora Sidebar" is clicked |

## Setup (development)

1. Create a new Apps Script project at <https://script.google.com/>.
2. Replace the default `Code.gs` content with this directory's
   `Code.gs`.
3. Add a new HTML file named `Sidebar` and paste in `Sidebar.html`'s
   content.
4. **Set Aurora's URL** via Script Properties:
   - File → Project properties → Script properties
   - Add: `AURORA_STUDIO_URL` = `https://your-aurora-host.example.com`
5. Open any spreadsheet → Extensions → Aurora → Open Aurora Sidebar.

## Publishing as a workspace add-on

Apps Script's workspace add-on flow (private to a Google Workspace
org) is the cleanest path for enterprise. For public marketplace:

1. Build the project as a workspace add-on (Apps Script editor → Deploy → Test deployments)
2. Submit via the Google Workspace Marketplace SDK
3. Requires OAuth verification + a privacy policy

## What's intentionally NOT here

- `appsscript.json` manifest — depends on how you deploy (workspace
  add-on vs. published add-on). Both are short; add when you decide.
- A standalone test harness — Apps Script doesn't run on Aurora's
  pytest, so we don't ship Python tests for it.

## Roadmap

| Feature | Status |
|---|---|
| Aurora menu + sidebar | ✅ scaffolded |
| Run on selection | ✅ scaffolded |
| Write findings to a new sheet | ✅ scaffolded |
| Configurable Aurora URL via Script Properties | ✅ scaffolded |
| Auth via OAuth (for hosted Aurora) | v0.2 |
| Inline severity highlighting on the source range | v0.2 |

## License

Apache 2.0.
