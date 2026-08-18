#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde_json::{json, Value};
use std::fs::OpenOptions;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};
use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};
use uuid::Uuid;

struct ManagedSidecar {
    child: Child,
    port: u16,
    token: String,
}

type SharedSidecar = Arc<Mutex<Option<ManagedSidecar>>>;

fn trace_path() -> Option<PathBuf> {
    std::env::var_os("WENQU_POC_TRACE_FILE").map(PathBuf::from)
}

fn append_trace(path: Option<&Path>, event: Value) {
    let Some(path) = path else {
        return;
    };
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(path) {
        let _ = writeln!(file, "{event}");
    }
}

fn project_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("src-tauri must be directly under the project root")
        .to_path_buf()
}

fn python_executable(root: &Path) -> PathBuf {
    std::env::var_os("WENQU_SIDECAR_PYTHON")
        .map(PathBuf::from)
        .unwrap_or_else(|| root.join(".venv").join("Scripts").join("python.exe"))
}

fn wait_for_health(port: u16, token: &str, timeout: Duration) -> Result<(), String> {
    let deadline = Instant::now() + timeout;
    let mut last_error = "sidecar did not answer".to_string();
    while Instant::now() < deadline {
        match TcpStream::connect_timeout(
            &format!("127.0.0.1:{port}")
                .parse()
                .map_err(|error| format!("invalid sidecar address: {error}"))?,
            Duration::from_millis(250),
        ) {
            Ok(mut stream) => {
                let request = format!(
                    "GET /api/health HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nX-Wenqu-Desktop-Token: {token}\r\nConnection: close\r\n\r\n"
                );
                if stream.write_all(request.as_bytes()).is_ok() {
                    let mut response = String::new();
                    let _ = stream.read_to_string(&mut response);
                    if response.starts_with("HTTP/1.1 200") {
                        return Ok(());
                    }
                    last_error = response.lines().next().unwrap_or("empty response").to_string();
                }
            }
            Err(error) => last_error = error.to_string(),
        }
        thread::sleep(Duration::from_millis(100));
    }
    Err(format!("sidecar health check timed out: {last_error}"))
}

fn start_sidecar(trace: Option<&Path>) -> Result<ManagedSidecar, String> {
    let root = project_root();
    let python = python_executable(&root);
    if !python.is_file() {
        return Err(format!("Python sidecar runtime not found: {}", python.display()));
    }
    let token = format!("{}{}", Uuid::new_v4().simple(), Uuid::new_v4().simple());
    let mut child = Command::new(&python)
        .args(["-m", "backend.app.desktop_sidecar"])
        .current_dir(&root)
        .env("WENQU_DESKTOP_TOKEN", &token)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .map_err(|error| format!("failed to start FastAPI sidecar: {error}"))?;

    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "sidecar stdout was not captured".to_string())?;
    let mut reader = BufReader::new(stdout);
    let mut ready_line = String::new();
    reader
        .read_line(&mut ready_line)
        .map_err(|error| format!("failed to read sidecar handshake: {error}"))?;
    let ready: Value = serde_json::from_str(&ready_line)
        .map_err(|error| format!("invalid sidecar handshake {ready_line:?}: {error}"))?;
    let port = ready["port"]
        .as_u64()
        .and_then(|value| u16::try_from(value).ok())
        .ok_or_else(|| format!("sidecar handshake has no valid port: {ready_line}"))?;
    append_trace(trace, ready);

    thread::spawn(move || {
        for line in reader.lines().map_while(Result::ok) {
            eprintln!("[sidecar] {line}");
        }
    });
    wait_for_health(port, &token, Duration::from_secs(20))?;
    Ok(ManagedSidecar { child, port, token })
}

fn request_shutdown(port: u16, token: &str) {
    let Ok(mut stream) = TcpStream::connect_timeout(
        &format!("127.0.0.1:{port}")
            .parse()
            .expect("loopback shutdown address must be valid"),
        Duration::from_millis(500),
    ) else {
        return;
    };
    let request = format!(
        "POST /desktop/shutdown HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nX-Wenqu-Desktop-Token: {token}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
    );
    let _ = stream.write_all(request.as_bytes());
    let mut response = [0_u8; 128];
    let _ = stream.read(&mut response);
}

fn stop_sidecar(shared: &SharedSidecar, trace: Option<&Path>) {
    let Ok(mut slot) = shared.lock() else {
        return;
    };
    let Some(mut managed) = slot.take() else {
        return;
    };
    let pid = managed.child.id();
    request_shutdown(managed.port, &managed.token);
    let deadline = Instant::now() + Duration::from_secs(4);
    loop {
        match managed.child.try_wait() {
            Ok(Some(_)) => break,
            Ok(None) if Instant::now() < deadline => thread::sleep(Duration::from_millis(100)),
            _ => {
                let _ = managed.child.kill();
                let _ = managed.child.wait();
                break;
            }
        }
    }
    append_trace(trace, json!({"event": "sidecar_stopped", "pid": pid}));
}

fn main() {
    let trace = trace_path();
    let sidecar: SharedSidecar = Arc::new(Mutex::new(None));
    let setup_sidecar = Arc::clone(&sidecar);
    let cleanup_sidecar = Arc::clone(&sidecar);
    let setup_trace = trace.clone();
    let cleanup_trace = trace.clone();

    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .setup(move |app| {
            let managed = start_sidecar(setup_trace.as_deref())
                .map_err(|error| Box::<dyn std::error::Error>::from(error))?;
            let bootstrap_url = format!(
                "http://127.0.0.1:{}/desktop/bootstrap?token={}",
                managed.port, managed.token
            );
            *setup_sidecar.lock().expect("sidecar state poisoned") = Some(managed);

            let page_trace = setup_trace.clone();
            WebviewWindowBuilder::new(
                app,
                "main",
                WebviewUrl::External(bootstrap_url.parse().expect("bootstrap URL must parse")),
            )
            .title("文曲英语学习系统 · Tauri 2 PoC")
            .inner_size(1280.0, 800.0)
            .on_page_load(move |_window, payload| {
                append_trace(
                    page_trace.as_deref(),
                    json!({"event": "page_loaded", "url": payload.url().as_str()}),
                );
            })
            .build()?;

            if let Ok(raw) = std::env::var("WENQU_POC_AUTO_EXIT_MS") {
                if let Ok(delay_ms) = raw.parse::<u64>() {
                    let app_handle = app.handle().clone();
                    thread::spawn(move || {
                        thread::sleep(Duration::from_millis(delay_ms));
                        app_handle.exit(0);
                    });
                }
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build Tauri application");

    app.run(move |_app_handle, event| match event {
        RunEvent::ExitRequested { .. } | RunEvent::Exit => {
            stop_sidecar(&cleanup_sidecar, cleanup_trace.as_deref());
        }
        _ => {}
    });
}
