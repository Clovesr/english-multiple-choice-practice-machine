#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde_json::{json, Value};
use std::fs::OpenOptions;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdout, Command, Stdio};
use std::sync::{mpsc, Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};
use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};
use uuid::Uuid;

struct ManagedSidecar {
    child: Option<Child>,
    port: u16,
    token: String,
}

type SharedSidecar = Arc<Mutex<Option<ManagedSidecar>>>;

const HANDSHAKE_TIMEOUT: Duration = Duration::from_secs(10);
const HEALTH_TIMEOUT: Duration = Duration::from_secs(20);
const SHUTDOWN_TIMEOUT: Duration = Duration::from_secs(4);

impl ManagedSidecar {
    fn stop(&mut self, timeout: Duration) -> Option<u32> {
        let mut child = self.child.take()?;
        let pid = child.id();
        stop_child(&mut child, Some((self.port, &self.token)), timeout);
        Some(pid)
    }
}

impl Drop for ManagedSidecar {
    fn drop(&mut self) {
        // Covers setup errors and panics before Tauri's RunEvent cleanup runs.
        let _ = self.stop(Duration::from_secs(1));
    }
}

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

fn trace_page_url(url: &str) -> String {
    if url.contains("/desktop/bootstrap") {
        let base = url.split('?').next().unwrap_or(url);
        return format!("{base}?token=REDACTED");
    }
    url.to_string()
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

fn wait_for_health(
    child: &mut Child,
    port: u16,
    token: &str,
    timeout: Duration,
) -> Result<(), String> {
    let deadline = Instant::now() + timeout;
    let mut last_error = "sidecar did not answer".to_string();
    while Instant::now() < deadline {
        match child.try_wait() {
            Ok(Some(status)) => {
                return Err(format!("sidecar exited before health check: {status}"));
            }
            Err(error) => return Err(format!("failed to inspect sidecar process: {error}")),
            Ok(None) => {}
        }
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
                    last_error = response
                        .lines()
                        .next()
                        .unwrap_or("empty response")
                        .to_string();
                }
            }
            Err(error) => last_error = error.to_string(),
        }
        thread::sleep(Duration::from_millis(100));
    }
    Err(format!("sidecar health check timed out: {last_error}"))
}

fn sidecar_handshake(stdout: ChildStdout) -> mpsc::Receiver<Result<String, String>> {
    let (sender, receiver) = mpsc::sync_channel(1);
    thread::spawn(move || {
        let mut reader = BufReader::new(stdout);
        let mut ready_line = String::new();
        let result = reader
            .read_line(&mut ready_line)
            .map(|_| ready_line)
            .map_err(|error| format!("failed to read sidecar handshake: {error}"));
        let _ = sender.send(result);
        for line in reader.lines().map_while(Result::ok) {
            eprintln!("[sidecar] {line}");
        }
    });
    receiver
}

fn parse_ready_handshake(ready_line: &str, expected_pid: u32) -> Result<(u16, Value), String> {
    let ready: Value = serde_json::from_str(ready_line)
        .map_err(|error| format!("invalid sidecar handshake: {error}"))?;
    if ready["event"] != "sidecar_ready" || ready["pid"].as_u64() != Some(expected_pid.into()) {
        return Err("sidecar handshake event or PID did not match the child process".to_string());
    }
    let port = ready["port"]
        .as_u64()
        .and_then(|value| u16::try_from(value).ok())
        .filter(|value| *value > 0)
        .ok_or_else(|| "sidecar handshake has no valid port".to_string())?;
    Ok((port, ready))
}

fn wait_for_child(child: &mut Child, timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    loop {
        match child.try_wait() {
            Ok(Some(_)) => return true,
            Ok(None) if Instant::now() < deadline => thread::sleep(Duration::from_millis(100)),
            _ => return false,
        }
    }
}

fn stop_child(child: &mut Child, graceful_shutdown: Option<(u16, &str)>, timeout: Duration) {
    if let Some((port, token)) = graceful_shutdown {
        request_shutdown(port, token);
    }
    if !wait_for_child(child, timeout) {
        let _ = child.kill();
        let _ = child.wait();
    }
}

fn start_sidecar(trace: Option<&Path>) -> Result<ManagedSidecar, String> {
    let root = project_root();
    let python = python_executable(&root);
    if !python.is_file() {
        return Err(format!(
            "Python sidecar runtime not found: {}",
            python.display()
        ));
    }
    let token = format!("{}{}", Uuid::new_v4().simple(), Uuid::new_v4().simple());
    let mut child = Command::new(&python)
        .args(["-m", "backend.app.desktop_sidecar"])
        .current_dir(&root)
        .env("WENQU_DESKTOP_TOKEN", &token)
        .env("WENQU_DESKTOP_PARENT_PIPE", "1")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .map_err(|error| format!("failed to start FastAPI sidecar: {error}"))?;

    let expected_pid = child.id();
    let Some(stdout) = child.stdout.take() else {
        stop_child(&mut child, None, Duration::ZERO);
        return Err("sidecar stdout was not captured".to_string());
    };
    let handshake = sidecar_handshake(stdout);
    let ready_line = match handshake.recv_timeout(HANDSHAKE_TIMEOUT) {
        Ok(Ok(line)) => line,
        Ok(Err(error)) => {
            stop_child(&mut child, None, Duration::ZERO);
            return Err(error);
        }
        Err(error) => {
            stop_child(&mut child, None, Duration::ZERO);
            return Err(format!(
                "sidecar handshake timed out or disconnected: {error}"
            ));
        }
    };
    let (port, ready) = match parse_ready_handshake(&ready_line, expected_pid) {
        Ok(result) => result,
        Err(error) => {
            stop_child(&mut child, None, Duration::ZERO);
            return Err(error);
        }
    };
    append_trace(trace, ready);

    if let Err(error) = wait_for_health(&mut child, port, &token, HEALTH_TIMEOUT) {
        stop_child(&mut child, Some((port, &token)), Duration::from_secs(1));
        return Err(error);
    }
    Ok(ManagedSidecar {
        child: Some(child),
        port,
        token,
    })
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
    if let Some(pid) = managed.stop(SHUTDOWN_TIMEOUT) {
        append_trace(trace, json!({"event": "sidecar_stopped", "pid": pid}));
    }
}

fn main() {
    let trace = trace_path();
    let sidecar: SharedSidecar = Arc::new(Mutex::new(None));
    let setup_sidecar = Arc::clone(&sidecar);
    let cleanup_sidecar = Arc::clone(&sidecar);
    let setup_trace = trace.clone();
    let cleanup_trace = trace.clone();
    let single_instance_trace = trace.clone();

    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(
            move |app, _args, _cwd| {
                append_trace(
                    single_instance_trace.as_deref(),
                    json!({"event": "single_instance_handoff"}),
                );
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.unminimize();
                    let _ = window.show();
                    let _ = window.set_focus();
                }
            },
        ))
        .setup(move |app| {
            let managed = start_sidecar(setup_trace.as_deref())
                .map_err(|error| Box::<dyn std::error::Error>::from(error))?;
            let bootstrap_url = format!(
                "http://127.0.0.1:{}/desktop/bootstrap?token={}",
                managed.port, managed.token
            );
            *setup_sidecar.lock().expect("sidecar state poisoned") = Some(managed);

            let page_trace = setup_trace.clone();
            let window_result = WebviewWindowBuilder::new(
                app,
                "main",
                WebviewUrl::External(bootstrap_url.parse().expect("bootstrap URL must parse")),
            )
            .title("文曲英语学习系统 · Tauri 2 PoC")
            .inner_size(1280.0, 800.0)
            .on_page_load(move |_window, payload| {
                append_trace(
                    page_trace.as_deref(),
                    json!({
                        "event": "page_loaded",
                        "url": trace_page_url(payload.url().as_str())
                    }),
                );
            })
            .build();
            if let Err(error) = window_result {
                stop_sidecar(&setup_sidecar, setup_trace.as_deref());
                return Err(Box::<dyn std::error::Error>::from(error));
            }

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

#[cfg(test)]
mod tests {
    use super::{parse_ready_handshake, trace_page_url};

    #[test]
    fn ready_handshake_requires_expected_event_pid_and_port() {
        let (port, _) =
            parse_ready_handshake(r#"{"event":"sidecar_ready","pid":42,"port":43123}"#, 42)
                .expect("valid handshake");
        assert_eq!(port, 43123);
        assert!(
            parse_ready_handshake(r#"{"event":"sidecar_ready","pid":43,"port":43123}"#, 42)
                .is_err()
        );
        assert!(parse_ready_handshake(r#"{"event":"other","pid":42,"port":43123}"#, 42).is_err());
    }

    #[test]
    fn bootstrap_trace_never_contains_the_session_token() {
        let traced = trace_page_url(
            "http://127.0.0.1:43123/desktop/bootstrap?token=super-secret-session-token",
        );
        assert_eq!(
            traced,
            "http://127.0.0.1:43123/desktop/bootstrap?token=REDACTED"
        );
        assert!(!traced.contains("super-secret-session-token"));
    }
}
