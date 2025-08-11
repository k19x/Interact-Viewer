// main.js
(() => {
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => [...document.querySelectorAll(s)];

  const statusTxt = $("#statusTxt");
  const startBtn = $("#startBtn");
  const stopBtn = $("#stopBtn");
  const payloadTxt = $("#payloadTxt");
  const copyBtn = $("#copyPayload");
  const toggleLogBtn = $("#toggleLog");
  const logBox = $("#logBox");
  const clearBtn = $("#clearBtn");
  const tbody = $("#tbody");

  let currentFilter = "ALL";
  let events = [];
  let es; // EventSource
  let logVisible = false;

  // ---- utils ----
  const badge = (proto) => {
    const p = (proto || "").toUpperCase();
    const cls = p === "DNS" ? "dns" : "http";
    return `<span class="badge ${cls}">${p || "—"}</span>`;
  };

  const esc = (s) =>
    String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");

  function normalizeTs(ts) {
    if (ts == null) return Math.floor(Date.now() / 1000);
    if (typeof ts === "number") return Math.floor(ts > 1e12 ? ts / 1000 : ts);
    if (typeof ts === "string") {
      const n = Number(ts);
      if (Number.isFinite(n)) return Math.floor(n > 1e12 ? n / 1000 : n);
      const parsed = Date.parse(ts); // ISO8601
      if (Number.isFinite(parsed)) return Math.floor(parsed / 1000);
    }
    return Math.floor(Date.now() / 1000);
  }

  function summarizeHTTP(http) {
    if (!http) return "";
    const method = http.method || "";
    const path = http.path || "";
    let head = `${esc(method)} ${esc(path)}`;
    return `
      <details>
        <summary>${head}</summary>
        <div class="kv mono">
          <b>Headers</b><div>${esc(JSON.stringify(http.headers || {}, null, 2))}</div>
          <b>Body</b><div>${esc(http.body || "")}</div>
        </div>
      </details>`;
  }

  function summarizeDNS(dns) {
    if (!dns) return "";
    const line = `${dns.qname || ""} (${dns.qtype || ""})`;
    return `
      <details>
        <summary>${esc(line)}</summary>
        <div class="kv mono">
          <b>Query</b><div>${esc(dns.qname || "")}</div>
          <b>Type</b><div>${esc(dns.qtype || "")}</div>
        </div>
      </details>`;
  }

  function renderRow(evt) {
    const when = new Date(normalizeTs(evt.timestamp) * 1000).toLocaleString();
    const src = evt.source || "—";
    const hostOrQuery = evt.host || (evt.dns && evt.dns.qname) || "—";
    const details =
      (evt.protocol || "").toUpperCase() === "DNS" ? summarizeDNS(evt.dns) : summarizeHTTP(evt.http);

    return `
      <tr>
        <td><div class="card small">${esc(when)}</div></td>
        <td><div class="card mono">${esc(src)}</div></td>
        <td><div class="card mono">${esc(hostOrQuery)}</div></td>
        <td><div class="card">${badge(evt.protocol)}</div></td>
        <td><div class="card">${details}</div></td>
      </tr>`;
  }

  function applyFilter(list, filter) {
    if (filter === "ALL") return list;
    return list.filter((e) => (e.protocol || "").toUpperCase() === filter);
  }

  function renderTable() {
    const view = applyFilter(events, currentFilter);
    const html = view.slice().reverse().map(renderRow).join("");
    tbody.innerHTML = html || "";
  }

  // ---- Controls ----
  async function api(path, opts = {}) {
    const hasBody = typeof opts.body === "string" && opts.body.length > 0;
    const headers = hasBody ? { "Content-Type": "application/json" } : {};
    const res = await fetch(path, { ...opts, headers });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.json();
  }

  async function refreshLast() {
    try {
      const data = await api("/api/last");
      events = (data || []).map((e) => ({ ...e, timestamp: normalizeTs(e.timestamp) }));
      renderTable();
    } catch (_) {}
  }

  async function refreshStatus() {
    try {
      const st = await api("/api/status");
      const running = !!st.running;

      statusTxt.innerHTML = `${
        running ? '<span class="dot good"></span>' : '<span class="dot bad"></span>'
      }${running ? "Em execução" : "Parado"}`;

      startBtn.disabled = running;
      stopBtn.disabled = !running;

      payloadTxt.textContent = st.payload || "—";

      const tail = (st.log_tail || []).join("\n");
      if (logVisible) logBox.textContent = tail || "";
    } catch (_) {
      statusTxt.innerHTML = `<span class="dot bad"></span>Erro`;
    }
  }

  function connectSSE() {
    if (es) es.close();
    es = new EventSource("/stream");
    es.onmessage = (ev) => {
      try {
        const obj = JSON.parse(ev.data);
        obj.timestamp = normalizeTs(obj.timestamp);
        events.push(obj);
        if (events.length > 500) events = events.slice(-500);
        renderTable();
      } catch (_) {}
    };
    es.onerror = () => { /* auto-reconnect do navegador */ };
  }

  // ---- Listeners ----
  startBtn.addEventListener("click", async () => {
    startBtn.disabled = true;
    try {
      // Sem body => não envia Content-Type
      await api("/api/start", { method: "POST" });
      await refreshStatus();
    } catch (e) {
      alert("Falha ao iniciar: " + e.message);
    } finally {
      startBtn.disabled = false;
    }
  });

  stopBtn.addEventListener("click", async () => {
    stopBtn.disabled = true;
    try {
      await api("/api/stop", { method: "POST" });
      await refreshStatus();
    } catch (e) {
      alert("Falha ao parar: " + e.message);
    } finally {
      stopBtn.disabled = false;
    }
  });

  copyBtn.addEventListener("click", async () => {
    const val = payloadTxt.textContent.trim();
    if (!val || val === "—") return;
    try {
      await navigator.clipboard.writeText(val);
      copyBtn.textContent = "Copiado!";
    } catch {
      try {
        const ta = document.createElement("textarea");
        ta.value = val;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
        copyBtn.textContent = "Copiado!";
      } catch {
        alert("Não foi possível copiar para a área de transferência.");
      }
    }
    setTimeout(() => (copyBtn.textContent = "Copiar"), 1200);
  });

  toggleLogBtn.addEventListener("click", () => {
    logVisible = !logVisible;
    logBox.style.display = logVisible ? "block" : "none";
    toggleLogBtn.textContent = logVisible ? "Ocultar log do cliente" : "Mostrar log do cliente";
  });

  clearBtn.addEventListener("click", () => {
    events = [];
    renderTable();
  });

  $$(".chip").forEach((chip) =>
    chip.addEventListener("click", () => {
      $$(".chip").forEach((c) => c.classList.remove("on"));
      chip.classList.add("on");
      currentFilter = chip.dataset.filter || "ALL";
      renderTable();
    })
  );

  // ---- Bootstrap ----
  (async function init() {
    connectSSE();
    await refreshLast();
    await refreshStatus();
    setInterval(refreshStatus, 3000);
  })();
})();
