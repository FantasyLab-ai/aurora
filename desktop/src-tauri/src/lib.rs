// Aurora desktop shell — Rust entry. The webview takes over from here;
// no custom Rust commands are needed for Phase 1 (the JS calls Aurora's
// HTTP API directly at http://127.0.0.1:8001).
#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
