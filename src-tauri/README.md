# Tauri 2 sidecar PoC

This spike validates APP-01 through APP-04 without changing the Vue application:

- Tauri owns the FastAPI child process and waits for an authenticated health check.
- Python binds an operating-system-selected `127.0.0.1` port and never listens on LAN interfaces.
- A 256-bit per-launch token is exchanged for an HttpOnly, SameSite cookie; `/api/*` rejects missing or foreign-origin sessions.
- `tauri-plugin-single-instance` focuses the existing window on a second launch.
- Closing Tauri requests graceful uvicorn shutdown, waits four seconds, then force-cleans as a fallback.

This is deliberately a development PoC. It uses `.venv/Scripts/python.exe`; a release implementation must freeze the Python backend and its resources into a versioned external binary before enabling Tauri installers.

Build the existing Vue distribution, compile the Rust crate, and run the Windows smoke check from the repository root:

```powershell
corepack pnpm --dir frontend build
cargo build --manifest-path src-tauri/Cargo.toml
powershell -ExecutionPolicy Bypass -File tools/tauri_poc_smoke.ps1
```
