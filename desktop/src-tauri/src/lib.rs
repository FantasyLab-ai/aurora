// Aurora desktop shell -- Rust entry.
//
// Beyond hosting the webview, this now manages the bundled Aurora backend:
// on startup it spawns the PyInstaller-frozen `aurora-backend` executable
// (shipped as an app resource), and on exit it kills it. That's what makes
// the installed app self-contained -- the user double-clicks Aurora and the
// Flask backend comes up inside the app, no separate `python studio_api.py`.
//
// In dev (`npm run tauri dev`) there is no bundled resource, so the spawn
// is skipped and the shell talks to a separately-running backend (e.g. via
// launch.ps1). The spawn is therefore best-effort: if the resource is
// missing, we log and continue rather than failing the app.

use std::process::{Child, Command};
use std::sync::Mutex;
use tauri::{Manager, RunEvent};

/// Holds the spawned backend child so we can kill it on exit.
struct BackendGuard(Mutex<Option<Child>>);

/// Platform-specific name of the bundled backend executable.
#[cfg(windows)]
const BACKEND_EXE: &str = "aurora-backend.exe";
#[cfg(not(windows))]
const BACKEND_EXE: &str = "aurora-backend";

/// Resolve the bundled backend path and spawn it. Returns the Child on
/// success, or None when there's nothing to spawn (dev mode / missing
/// resource) so the caller can no-op gracefully.
fn spawn_backend(app: &tauri::AppHandle) -> Option<Child> {
    let resource_dir = match app.path().resource_dir() {
        Ok(d) => d,
        Err(e) => {
            eprintln!("[aurora] resource_dir unavailable: {e}");
            return None;
        }
    };
    // Bundled as resources/aurora-backend/ (a PyInstaller onedir folder).
    let backend = resource_dir
        .join("aurora-backend")
        .join(BACKEND_EXE);

    if !backend.exists() {
        eprintln!(
            "[aurora] bundled backend not found at {} -- assuming dev mode \
             (expecting a separately-running studio_api on :8001).",
            backend.display()
        );
        return None;
    }

    eprintln!("[aurora] starting bundled backend: {}", backend.display());
    match Command::new(&backend)
        .env("AURORA_PORT", "8001")
        .env("AURORA_NO_BROWSER", "1")   // the desktop window IS the UI
        .env("AURORA_HOST", "127.0.0.1") // loopback only
        .spawn()
    {
        Ok(child) => {
            eprintln!("[aurora] backend started (pid {}).", child.id());
            Some(child)
        }
        Err(e) => {
            eprintln!("[aurora] failed to start backend: {e}");
            None
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .manage(BackendGuard(Mutex::new(None)))
        .setup(|app| {
            let handle = app.handle().clone();
            if let Some(child) = spawn_backend(&handle) {
                let guard = app.state::<BackendGuard>();
                *guard.0.lock().unwrap() = Some(child);
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|app_handle, event| {
        // Kill the backend when the app is exiting so we never leave an
        // orphaned Flask process bound to :8001.
        if let RunEvent::Exit = event {
            if let Some(guard) = app_handle.try_state::<BackendGuard>() {
                if let Some(mut child) = guard.0.lock().unwrap().take() {
                    eprintln!("[aurora] stopping backend (pid {})...", child.id());
                    let _ = child.kill();
                    let _ = child.wait();
                }
            }
        }
    });
}
