#!/usr/bin/env python3
import os
import re
import json
import time
import threading
import subprocess
from pathlib import Path
from shutil import which
from datetime import datetime
from flask import Flask, Response, jsonify, send_from_directory, request, stream_with_context

# ===========================
# Helpers de normalização
# ===========================
def resolve_source(evt: dict, http_parsed: dict) -> str:
    src = (
        evt.get("remote_address")
        or evt.get("remote_addr")
        or evt.get("remote-address")
        or evt.get("source")
        or ""
    )
    if not src and http_parsed:
        hdrs = (http_parsed or {}).get("headers") or {}
        src = (hdrs.get("X-Forwarded-For") or hdrs.get("x-forwarded-for")
               or hdrs.get("X-Real-IP") or hdrs.get("x-real-ip") or "")
    return src

def resolve_host(evt: dict, http_parsed: dict) -> str:
    host = evt.get("host") or evt.get("full-id") or evt.get("domain") or ""
    if not host and http_parsed:
        hdrs = (http_parsed or {}).get("headers") or {}
        host = hdrs.get("Host") or hdrs.get("host") or ""
    return host

def normalize_ts(ts):
    if isinstance(ts, (int, float)):
        return int(ts if ts < 1e12 else ts / 1000)
    if isinstance(ts, str):
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return int(dt.timestamp())
        except Exception:
            try:
                n = float(ts)
                return int(n if n < 1e12 else n / 1000)
            except Exception:
                return int(time.time())
    return int(time.time())

def parse_http_from_raw(raw_request: str):
    if not raw_request:
        return {}
    raw = raw_request.replace("\r\n", "\n")
    parts = raw.split("\n\n", 1)
    head = parts[0]
    body = parts[1] if len(parts) > 1 else ""
    lines = head.splitlines()
    if not lines:
        return {}
    request_line = lines[0].strip().split()
    method = request_line[0] if len(request_line) > 0 else ""
    path = request_line[1] if len(request_line) > 1 else ""
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip()] = v.strip()
    return {"method": method, "path": path, "headers": headers, "body": body}

# ===========================
# Parâmetros portáveis (ENV)
# ===========================
APP_DIR = Path(__file__).parent.resolve()

HOST_DATA_DIR = Path(
    os.getenv("INTERACT_DATA_DIR", Path.home() / ".interactsh-viewer" / "data")
)
HOST_DATA_DIR.mkdir(parents=True, exist_ok=True)
HOST_DATA_FILE = str(HOST_DATA_DIR / "interactions.ndjson")
DATA_FILE = Path(os.getenv("DATA_FILE", HOST_DATA_FILE))

CONTAINER_DATA_FILE = os.getenv("INTERACT_CONTAINER_DATA", "/data/interactions.ndjson")
IMAGE = os.getenv("INTERACT_IMAGE", "projectdiscovery/interactsh-client")
DOCKER_NAME = os.getenv("INTERACT_CONTAINER_NAME", "interactsh-client")
DEFAULT_SERVER = os.getenv("INTERACT_SERVER", "https://oast.pro")

DOCKER_BIN = os.getenv("CONTAINER_BIN") or which("docker") or which("podman") or "docker"
PAYLOAD_RE = re.compile(r"([a-z0-9]{10,}\.oast\.(?:pro|live))", re.I)

def _maybe_sudo(cmd: list) -> list:
    return (["sudo"] + cmd) if os.getenv("CONTAINER_SUDO", "0") in ("1", "true", "yes") else cmd

# ===========================
# Estado de UI
# ===========================
app = Flask(__name__, static_folder=".", static_url_path="")
PROC_STATE = {"proc": None, "payload": None, "started_at": None, "log_tail": []}
LAST_EVENTS, MAX_CACHE = [], 500

# ===========================
# Leitura do NDJSON (tail)
# ===========================
def follow_ndjson():
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.touch(exist_ok=True)

    with DATA_FILE.open("r", encoding="utf-8", errors="ignore") as f:
        f.seek(0, 2)  # tail -f
        while True:
            line = f.readline()
            if not line:
                time.sleep(0.2)
                continue
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue

            proto = evt.get("protocol") or evt.get("type")
            ts_raw = evt.get("timestamp") or evt.get("time") or int(time.time())
            ts = normalize_ts(ts_raw)

            raw_http = evt.get("raw-request") or evt.get("request") or ""
            http_parsed = parse_http_from_raw(raw_http) if raw_http else {}

            src = resolve_source(evt, http_parsed)
            host = resolve_host(evt, http_parsed)

            qname = evt.get("qname") or evt.get("query_name") or ""
            qtype = evt.get("qtype") or evt.get("query_type") or ""

            norm = {
                "protocol": (proto or "").upper(),
                "timestamp": ts,
                "source": src or "",
                "host": host or "",
                "dns": {"qname": qname, "qtype": qtype} if (qname or qtype) else None,
                "http": http_parsed if http_parsed else None,
                "raw": raw_http or None,
            }

            LAST_EVENTS.append(norm)
            if len(LAST_EVENTS) > MAX_CACHE:
                del LAST_EVENTS[: len(LAST_EVENTS) - MAX_CACHE]

            yield norm

def tail_worker():
    for _ in follow_ndjson():
        pass

def warmup_tail():
    if not DATA_FILE.exists():
        return
    try:
        lines = DATA_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()[-200:]
        for line in lines:
            try:
                evt = json.loads(line)

                proto = evt.get("protocol") or evt.get("type")
                ts_raw = evt.get("timestamp") or evt.get("time") or int(time.time())
                ts = normalize_ts(ts_raw)

                raw_http = evt.get("raw-request") or evt.get("request") or ""
                http_parsed = parse_http_from_raw(raw_http) if raw_http else {}

                src = resolve_source(evt, http_parsed)
                host = resolve_host(evt, http_parsed)

                qname = evt.get("qname") or evt.get("query_name") or ""
                qtype = evt.get("qtype") or evt.get("query_type") or ""

                norm = {
                    "protocol": (proto or "").upper(),
                    "timestamp": ts,
                    "source": src or "",
                    "host": host or "",
                    "dns": {"qname": qname, "qtype": qtype} if (qname or qtype) else None,
                    "http": http_parsed if http_parsed else None,
                    "raw": raw_http or None,
                }
                LAST_EVENTS.append(norm)
            except Exception:
                continue

        if len(LAST_EVENTS) > MAX_CACHE:
            del LAST_EVENTS[: len(LAST_EVENTS) - MAX_CACHE]
    except Exception:
        pass

# ===========================
# Docker/Podman helpers
# ===========================
def run_cmd(cmd: list, timeout=25):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode == 0, (p.stdout or "").strip(), (p.stderr or "").strip()
    except Exception as e:
        return False, "", str(e)

def docker_is_running():
    ok, out, _ = run_cmd(_maybe_sudo([DOCKER_BIN, "inspect", "-f", "{{.State.Running}}", DOCKER_NAME]))
    return ok and out.lower().startswith("true")

def start_docker_client(server: str | None):
    # garante pasta/arquivo no HOST
    HOST_DATA_DIR.mkdir(parents=True, exist_ok=True)
    Path(HOST_DATA_FILE).touch(exist_ok=True)

    cmd = _maybe_sudo([
        DOCKER_BIN, "run", "-d", "--name", DOCKER_NAME,
        "-v", f"{HOST_DATA_DIR}:/data",
        IMAGE,
        "-server", server or DEFAULT_SERVER,
        "-json", "-v", "-o", CONTAINER_DATA_FILE,
    ])
    ok, out, err = run_cmd(cmd)
    if not ok:
        return False, (err or out), None

    # segue logs do container para extrair payload e popular log_tail
    proc = subprocess.Popen(
        _maybe_sudo([DOCKER_BIN, "logs", "-f", DOCKER_NAME]),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    return True, out, proc

def stop_docker_client():
    ok, out, err = run_cmd(_maybe_sudo([DOCKER_BIN, "rm", "-f", DOCKER_NAME]))
    return ok, (err or out)

def extract_last_payload():
    # tenta no cache
    for ev in reversed(LAST_EVENTS):
        host = (ev.get("host") or "")
        if re.search(r"\.oast\.(?:pro|live)\.?$", host):
            return host
    # fallback no arquivo
    if DATA_FILE.exists():
        try:
            lines = DATA_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()[-400:]
            for line in reversed(lines):
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                host = obj.get("host") or obj.get("full-id") or obj.get("domain") or ""
                if re.search(r"\.oast\.(?:pro|live)\.?$", host):
                    return host
        except Exception:
            pass
    return None

def reader_thread(proc: subprocess.Popen):
    # segue docker logs pra payload + log_tail
    for line in iter(proc.stdout.readline, ""):
        line = line.rstrip("\n")
        if not line:
            continue
        PROC_STATE["log_tail"].append(line)
        if len(PROC_STATE["log_tail"]) > 200:
            PROC_STATE["log_tail"] = PROC_STATE["log_tail"][-200:]

        if PROC_STATE["payload"] is None:
            m = PAYLOAD_RE.search(line)
            if m:
                PROC_STATE["payload"] = m.group(1)

# ===========================
# Rotas (SSE + API)
# ===========================
@app.route("/stream")
def stream():
    @stream_with_context
    def event_stream():
        gen = follow_ndjson()
        last_heartbeat = time.time()
        while True:
            try:
                evt = next(gen)
                yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
            except StopIteration:
                time.sleep(0.2)
            now = time.time()
            if now - last_heartbeat >= 15:
                yield ": ping\n\n"
                last_heartbeat = now

    headers = {
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return Response(event_stream(), mimetype="text/event-stream", headers=headers)

@app.route("/api/last")
def api_last():
    if not LAST_EVENTS:
        warmup_tail()
    return jsonify(LAST_EVENTS[-200:])

@app.route("/")
def index():
    return send_from_directory(".", "index.html")

@app.route("/favicon.ico")
def favicon():
    return ("", 204)

@app.route("/api/start", methods=["POST"])
def api_start():
    data = request.get_json(silent=True) or {}
    force = bool(data.get("force", False))
    truncate = bool(data.get("truncate", True))
    server = data.get("server")  # opcional, ex.: "https://oast.pro"

    # se container já estiver rodando e não for force, retorna status
    if docker_is_running() and not force:
        return jsonify({"ok": True, "message": "Já está em execução", "payload": PROC_STATE["payload"]}), 200

    # derruba seguidor de logs e container antigo
    if PROC_STATE["proc"] and PROC_STATE["proc"].poll() is None:
        try:
            PROC_STATE["proc"].terminate()
            PROC_STATE["proc"].wait(timeout=5)
        except Exception:
            try:
                PROC_STATE["proc"].kill()
            except Exception:
                pass
    stop_docker_client()

    # limpa arquivo/cache se pedido
    if truncate:
        try:
            Path(HOST_DATA_FILE).write_text("", encoding="utf-8")
        except Exception:
            pass
        LAST_EVENTS.clear()

    # sobe novo container e segue logs
    ok, msg, logproc = start_docker_client(server)
    if not ok:
        return jsonify({"ok": False, "error": msg}), 500

    PROC_STATE.update({"proc": logproc, "payload": None, "started_at": time.time(), "log_tail": []})
    threading.Thread(target=reader_thread, args=(logproc,), daemon=True).start()
    return jsonify({"ok": True, "message": "Processo iniciado"}), 200

@app.route("/api/stop", methods=["POST"])
def api_stop():
    if PROC_STATE["proc"] and PROC_STATE["proc"].poll() is None:
        try:
            PROC_STATE["proc"].terminate()
            PROC_STATE["proc"].wait(timeout=5)
        except Exception:
            try:
                PROC_STATE["proc"].kill()
            except Exception:
                pass
    stop_docker_client()
    PROC_STATE.update({"proc": None, "payload": None, "started_at": None, "log_tail": []})
    return jsonify({"ok": True})

@app.route("/api/restart", methods=["POST"])
def api_restart():
    _ = api_stop()
    data = request.get_json(silent=True) or {}
    data.setdefault("truncate", True)
    data["force"] = True
    with app.test_request_context(json=data):
        return api_start()

@app.route("/api/status")
def api_status():
    running = docker_is_running()
    payload = extract_last_payload() or PROC_STATE.get("payload")
    return jsonify({
        "running": running,
        "payload": payload,
        "started_at": PROC_STATE.get("started_at"),
        "log_tail": PROC_STATE.get("log_tail", [])[-30:],
    })

# ===========================
# Boot
# ===========================
if __name__ == "__main__":
    warmup_tail()
    threading.Thread(target=tail_worker, daemon=True).start()
    app.run(host="0.0.0.0", port=5000, debug=True)
