import os
import re
import json
import time
import threading
import subprocess
from pathlib import Path
from flask import Flask, Response, jsonify, send_from_directory, request, stream_with_context
from datetime import datetime, timezone

def normalize_ts(ts):
    # aceita epoch s/ms, float, ou ISO8601
    if isinstance(ts, (int, float)):
        return int(ts if ts < 1e12 else ts/1000)
    if isinstance(ts, str):
        try:
            dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
            return int(dt.timestamp())
        except Exception:
            try:
                n = float(ts)
                return int(n if n < 1e12 else n/1000)
            except Exception:
                return int(time.time())
    return int(time.time())

APP_DIR = Path(__file__).parent.resolve()
DATA_FILE = Path(os.getenv("DATA_FILE") or (APP_DIR / "interactions.ndjson"))

app = Flask(__name__, static_folder=".", static_url_path="")

# Estado do processo do interactsh-client
PROC_STATE = {"proc": None, "payload": None, "started_at": None, "log_tail": []}

# Cache simples em memória para /api/last
LAST_EVENTS, MAX_CACHE = [], 500

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

def follow_ndjson():
    # garante pasta/arquivo
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
            src = evt.get("remote_address") or evt.get("remote_addr") or ""
            host = evt.get("host") or evt.get("full-id") or evt.get("domain") or ""
            raw_http = evt.get("raw-request") or evt.get("request") or ""
            http_parsed = parse_http_from_raw(raw_http) if raw_http else {}
            qname = evt.get("qname") or evt.get("query_name") or ""
            qtype = evt.get("qtype") or evt.get("query_type") or ""

            norm = {
                "protocol": (proto or "").upper(),
                "timestamp": ts,
                "source": src,
                "host": host,
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
            # heartbeat a cada 15s
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
    if not LAST_EVENTS:  # se ninguém tailou ainda, puxa do arquivo
        warmup_tail()
    return jsonify(LAST_EVENTS[-200:])


@app.route("/")
def index():
    return send_from_directory(".", "index.html")

@app.route("/favicon.ico")
def favicon():
    return ("", 204)

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
                src = evt.get("remote_address") or evt.get("remote_addr") or ""
                host = evt.get("host") or evt.get("full-id") or evt.get("domain") or ""
                raw_http = evt.get("raw-request") or evt.get("request") or ""
                http_parsed = parse_http_from_raw(raw_http) if raw_http else {}
                qname = evt.get("qname") or evt.get("query_name") or ""
                qtype = evt.get("qtype") or evt.get("query_type") or ""
                norm = {
                    "protocol": (proto or "").upper(),
                    "timestamp": ts,
                    "source": src,
                    "host": host,
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

# ---------- Controls for interactsh-client ----------
PAYLOAD_RE = re.compile(r"([a-z0-9]{16,}\.oast\.(?:live|pro))", re.I)

def reader_thread(proc):
    for line in iter(proc.stdout.readline, ""):
        line = line.rstrip()
        if not line:
            continue
        PROC_STATE["log_tail"].append(line)
        if len(PROC_STATE["log_tail"]) > 200:
            PROC_STATE["log_tail"] = PROC_STATE["log_tail"][-200:]
        if PROC_STATE["payload"] is None:
            m = PAYLOAD_RE.search(line)
            if m:
                PROC_STATE["payload"] = m.group(1)

@app.route("/api/start", methods=["POST"])
def api_start():
    if PROC_STATE["proc"] and PROC_STATE["proc"].poll() is None:
        return jsonify({"ok": True, "message": "Já está em execução", "payload": PROC_STATE["payload"]}), 200

    mode = os.getenv("INTERACT_MODE", "LOCAL").upper()  # LOCAL | EXEC
    host_data_file = os.getenv("DATA_FILE", str(DATA_FILE))  # caminho NO HOST
    container = os.getenv("INTERACT_CONTAINER", "interactsh-client")
    container_data_file = os.getenv("CONTAINER_DATA_FILE", "/data/interactions.ndjson")  # caminho NO CONTAINER
    server_env = os.getenv("INTERACT_SERVER")  # ex.: https://oast.pro

    data = request.get_json(silent=True) or {}
    body_cmd = data.get("cmd") or []

    if mode == "EXEC":
        if body_cmd:
            cmd = body_cmd
        else:
            cmd = ["docker", "exec", "-i", container, "interactsh-client"]
            if server_env:
                cmd += ["-server", server_env]
            cmd += ["-json", "-v", "-o", container_data_file]
    else:
        if body_cmd:
            cmd = body_cmd
        else:
            cmd = ["interactsh-client"]
            if server_env:
                cmd += ["-server", server_env]
            cmd += ["-json", "-v", "-o", host_data_file]

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=str(APP_DIR)
        )
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "interactsh-client não encontrado no PATH."}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    PROC_STATE.update({"proc": proc, "payload": None, "started_at": time.time(), "log_tail": []})
    threading.Thread(target=reader_thread, args=(proc,), daemon=True).start()
    return jsonify({"ok": True, "message": "Processo iniciado"}), 200

@app.route("/api/status")
def api_status():
    running = PROC_STATE["proc"] is not None and PROC_STATE["proc"].poll() is None
    return jsonify({
        "running": running,
        "payload": PROC_STATE["payload"],
        "started_at": PROC_STATE["started_at"],
        "log_tail": PROC_STATE["log_tail"][-30:],
    })

@app.route("/api/stop", methods=["POST"])
def api_stop():
    if PROC_STATE["proc"] and PROC_STATE["proc"].poll() is None:
        PROC_STATE["proc"].terminate()
        try:
            PROC_STATE["proc"].wait(timeout=5)
        except Exception:
            PROC_STATE["proc"].kill()
    PROC_STATE.update({"proc": None, "payload": None, "started_at": None, "log_tail": []})
    return jsonify({"ok": True})

if __name__ == "__main__":
    warmup_tail()
    threading.Thread(target=tail_worker, daemon=True).start()
    app.run(host="0.0.0.0", port=5000, debug=True)

