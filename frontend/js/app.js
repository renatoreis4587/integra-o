/* ==========================================================================
   Integra-O — Sistema de Balanças
   Frontend: WebSocket em tempo real, comandos, configuração e histórico.
   ========================================================================== */

const API = ""; // mesmo host
const socket = io();

let currentState = {
  connected: false, status: "desconectado", gross: 0, tare: 0, net: 0,
  stable: false, overload: false, negative: false, unit: "kg", counter: 0,
};
let config = null;
let history = [];
let lastStableTs = 0;

/* ------------------------------ Utilidades ------------------------------ */
const $ = (id) => document.getElementById(id);
const fmt = (v, d = 3) => (Number(v) || 0).toFixed(d);

function toast(msg, type = "info") {
  const el = document.createElement("div");
  el.className = "toast " + type;
  el.textContent = msg;
  $("toasts").appendChild(el);
  setTimeout(() => { el.style.opacity = "0"; setTimeout(() => el.remove(), 300); }, 3500);
}

async function api(path, method = "GET", body = null) {
  const opts = { method, headers: { "Content-Type": "application/json" } };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(API + path, opts);
  return res.json();
}

/* ------------------------------ Render peso ----------------------------- */
function renderState(s) {
  currentState = { ...currentState, ...s };
  const dp = (config && config.weighing && config.weighing.decimal_places) ?? 3;

  const valueEl = $("weightValue");
  valueEl.textContent = fmt(s.net, dp);
  valueEl.classList.toggle("unstable", !s.stable && s.connected);
  valueEl.classList.toggle("negative", s.negative);
  valueEl.classList.toggle("overload", s.overload);

  $("weightUnit").textContent = s.unit || "kg";
  $("metaGross").textContent = fmt(s.gross, dp);
  $("metaTare").textContent = fmt(s.tare, dp);
  $("metaNet").textContent = fmt(s.net, dp);
  $("metaConn").textContent = s.connection || "—";

  const badge = $("stableBadge");
  if (s.overload) { badge.textContent = "SOBRECARGA"; badge.classList.remove("on"); }
  else if (s.stable && s.connected) { badge.textContent = "Estável"; badge.classList.add("on"); }
  else { badge.textContent = "Instável"; badge.classList.remove("on"); }

  // Barra de estabilidade (visual: 100% quando estável)
  const fill = $("stabilityFill");
  fill.style.width = s.stable && s.connected ? "100%" : (s.connected ? "45%" : "0%");

  // Status pill
  const pill = $("statusPill");
  pill.classList.remove("on", "warn");
  if (s.connected) pill.classList.add("on");
  else if ((s.status || "").startsWith("erro") || (s.status || "").includes("reconect")) pill.classList.add("warn");
  $("statusText").textContent = s.status || "desconectado";
  $("btnConnectLabel").textContent = s.connected ? "Desconectar" : "Conectar";

  $("statCounter").textContent = s.counter ?? 0;

  // Monitor de bytes crus (diagnóstico de protocolo).
  const mon = $("rawMonitor");
  if (mon) mon.textContent = s.raw_hex || "—";
}

/* ------------------------------ Histórico ------------------------------- */
function renderHistory() {
  const body = $("historyBody");
  if (!history.length) {
    body.innerHTML = '<tr><td colspan="9" class="muted" style="text-align:center;padding:24px">Nenhuma pesagem registrada.</td></tr>';
    return;
  }
  const dp = (config && config.weighing && config.weighing.decimal_places) ?? 3;
  body.innerHTML = history.slice().reverse().map((r) => `
    <tr>
      <td>${r.id ?? ""}</td>
      <td>${(r.timestamp || "").replace("T", " ")}</td>
      <td class="num"><b>${fmt(r.net, dp)}</b></td>
      <td>${r.unit || ""}</td>
      <td class="num">${fmt(r.tare, dp)}</td>
      <td class="num">${fmt(r.gross, dp)}</td>
      <td>${r.stable ? '<span class="badge ok">Sim</span>' : '<span class="badge">Não</span>'}</td>
      <td>${r.auto ? '<span class="badge auto">Auto</span>' : '<span class="badge manual">Manual</span>'}</td>
      <td>${r.operator || ""}</td>
    </tr>`).join("");
}

/* ------------------------------ Logs ------------------------------------ */
function appendLog(entry) {
  const box = $("logs");
  const line = document.createElement("div");
  line.className = "log-line " + (entry.level || "info");
  line.innerHTML = `<span class="ts">${(entry.timestamp || "").split("T")[1] || entry.timestamp}</span><span class="msg">${entry.message}</span>`;
  box.appendChild(line);
  box.scrollTop = box.scrollHeight;
  while (box.children.length > 300) box.removeChild(box.firstChild);
}

/* ------------------------------ SocketIO -------------------------------- */
socket.on("connect", () => toast("Conectado ao servidor", "info"));
socket.on("disconnect", () => toast("Desconectado do servidor", "error"));
socket.on("weight", renderState);
socket.on("status", (p) => renderState({ ...currentState, ...p }));
socket.on("saved", (rec) => {
  history.push(rec);
  renderHistory();
  toast(`Pesagem salva: ${fmt(rec.net)} ${rec.unit}`, "info");
});
socket.on("log", appendLog);
socket.on("log_history", (p) => (p.logs || []).forEach(appendLog));
socket.on("command", (p) => {
  if (p.command === "tare") toast(`Tara definida: ${fmt(p.value)}`, "info");
  if (p.command === "clear_tare") toast("Tara removida", "info");
  if (p.command === "zero") toast("Balança zerada", "info");
});

/* ------------------------------ Comandos -------------------------------- */
async function command(name, body = {}) {
  try {
    const res = await api(`/api/command/${name}`, "POST", body);
    if (!res.ok) toast(res.error || "Erro no comando", "error");
    if (res.state) renderState(res.state);
    return res;
  } catch (e) { toast("Erro: " + e.message, "error"); }
}

$("btnTare").onclick = () => command("tare");
$("btnTareManual").onclick = () => {
  const v = prompt("Informe a tara (em " + (currentState.unit || "kg") + "):", "0");
  if (v !== null) command("tare", { value: v });
};
$("btnClearTare").onclick = () => command("clear_tare");
$("btnZero").onclick = () => command("zero");
$("btnRecord").onclick = async () => {
  const res = await command("record");
  if (res && res.record) toast(`Salvo: ${fmt(res.record.net)} ${res.record.unit}`, "info");
};
$("btnConnect").onclick = () => {
  if (currentState.connected) command("disconnect");
  else command("connect");
};
$("btnClearHistory").onclick = async () => {
  if (confirm("Limpar todo o histórico?")) {
    await api("/api/history", "DELETE");
    history = [];
    renderHistory();
  }
};
$("btnExport").onclick = () => {
  if (!history.length) return toast("Sem dados para exportar", "warning");
  const dp = (config && config.weighing && config.weighing.decimal_places) ?? 3;
  const head = "id,timestamp,net,unit,tare,gross,stable,auto,operator\n";
  const rows = history.map((r) =>
    [r.id, r.timestamp, fmt(r.net, dp), r.unit, fmt(r.tare, dp), fmt(r.gross, dp),
     r.stable, r.auto, (r.operator || "").replace(/,/g, " ")].join(",")).join("\n");
  const blob = new Blob([head + rows], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "pesagens_" + new Date().toISOString().slice(0, 10) + ".csv";
  a.click();
};

/* ------------------------------ Tabs ------------------------------------ */
document.querySelectorAll(".tab").forEach((tab) => {
  tab.onclick = () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
    tab.classList.add("active");
    $(tab.dataset.tab).classList.add("active");
  };
});

/* ------------------------------ Config UI ------------------------------- */
function setVal(id, v) { const el = $(id); if (el) el.value = v ?? ""; }
function getVal(id) { const el = $(id); return el ? el.value : ""; }
function getBool(id) { return getVal(id) === "true"; }

function toggleConnFields() {
  const type = getVal("connType");
  $("serialFields").classList.toggle("hidden", type !== "serial");
  $("tcpFields").classList.toggle("hidden", type !== "tcp");
}

function toggleCloudFields() {
  const p = getVal("cloudProvider");
  $("webdavFields").classList.toggle("hidden", p !== "webdav");
  $("onedriveFields").classList.toggle("hidden", p !== "onedrive_api");
  $("gdriveFields").classList.toggle("hidden", p !== "gdrive_api");
}

function toggleRegexField() {
  $("regexField").classList.toggle("hidden", getVal("protoType") !== "custom_regex");
}

function fillConfig(cfg) {
  config = cfg;
  $("companyName").textContent = cfg.ui.company_name || "Integra-O";

  // Conexão
  setVal("connType", cfg.connection.type);
  setVal("serialPort", cfg.connection.serial.port);
  setVal("serialBaud", cfg.connection.serial.baudrate);
  setVal("serialBytesize", cfg.connection.serial.bytesize);
  setVal("serialParity", cfg.connection.serial.parity);
  setVal("serialStopbits", cfg.connection.serial.stopbits);
  setVal("tcpHost", cfg.connection.tcp.host);
  setVal("tcpPort", cfg.connection.tcp.port);
  setVal("tcpTimeout", cfg.connection.tcp.timeout);
  setVal("autoReconnect", String(cfg.connection.auto_reconnect));
  setVal("reconnectDelay", cfg.connection.reconnect_delay);

  // Protocolo
  setVal("protoType", cfg.protocol.type);
  setVal("protoDecimals", cfg.protocol.decimal_places);
  setVal("protoMultiplier", cfg.protocol.multiplier);
  setVal("protoTerminator", cfg.protocol.line_terminator);
  setVal("protoEncoding", cfg.protocol.encoding);
  setVal("protoRegex", cfg.protocol.regex);

  // Pesagem
  setVal("weighUnit", cfg.weighing.unit);
  setVal("weighTare", cfg.weighing.default_tare);
  setVal("weighThreshold", cfg.weighing.stability_threshold);
  setVal("weighStabilityTime", cfg.weighing.stability_time);
  setVal("weighMin", cfg.weighing.min_weight);
  setVal("weighAutoSave", String(cfg.weighing.auto_save));
  setVal("weighAutoInterval", cfg.weighing.auto_save_interval);

  // Nuvem
  setVal("cloudProvider", cfg.cloud.provider);
  setVal("cloudEnabled", String(cfg.cloud.enabled));
  setVal("cloudFormat", cfg.cloud.format);
  setVal("cloudFilename", cfg.cloud.filename);
  setVal("cloudFolder", cfg.cloud.local_folder);
  setVal("webdavUrl", cfg.cloud.webdav.url);
  setVal("webdavUser", cfg.cloud.webdav.username);
  setVal("webdavPass", cfg.cloud.webdav.password);
  setVal("odTenant", cfg.cloud.onedrive_api.tenant_id);
  setVal("odClientId", cfg.cloud.onedrive_api.client_id);
  setVal("odClientSecret", cfg.cloud.onedrive_api.client_secret);
  setVal("odRefreshToken", cfg.cloud.onedrive_api.refresh_token);
  setVal("odFolder", cfg.cloud.onedrive_api.folder);
  setVal("gdCreds", cfg.cloud.gdrive_api.credentials_file);
  setVal("gdToken", cfg.cloud.gdrive_api.token_file);
  setVal("gdFolderId", cfg.cloud.gdrive_api.folder_id);
  setVal("gdFolder", cfg.cloud.gdrive_api.folder);

  // Sistema
  setVal("srvHost", cfg.server.host);
  setVal("srvPort", cfg.server.port);
  setVal("uiCompany", cfg.ui.company_name);
  setVal("uiOperator", cfg.ui.operator);

  toggleConnFields();
  toggleCloudFields();
  toggleRegexField();
}

function collectConfig() {
  return {
    connection: {
      type: getVal("connType"),
      serial: {
        port: getVal("serialPort"),
        baudrate: parseInt(getVal("serialBaud")) || 9600,
        bytesize: parseInt(getVal("serialBytesize")) || 8,
        parity: getVal("serialParity"),
        stopbits: parseFloat(getVal("serialStopbits")) || 1,
        timeout: 1.0,
      },
      tcp: {
        host: getVal("tcpHost"),
        port: parseInt(getVal("tcpPort")) || 9000,
        timeout: parseFloat(getVal("tcpTimeout")) || 3.0,
      },
      auto_reconnect: getBool("autoReconnect"),
      reconnect_delay: parseFloat(getVal("reconnectDelay")) || 3.0,
    },
    protocol: {
      type: getVal("protoType"),
      encoding: getVal("protoEncoding"),
      line_terminator: getVal("protoTerminator"),
      regex: getVal("protoRegex"),
      decimal_places: parseInt(getVal("protoDecimals")) || 0,
      multiplier: parseFloat(getVal("protoMultiplier")) || 1.0,
    },
    weighing: {
      unit: getVal("weighUnit"),
      default_tare: parseFloat(getVal("weighTare")) || 0,
      stability_threshold: parseFloat(getVal("weighThreshold")) || 0.002,
      stability_time: parseFloat(getVal("weighStabilityTime")) || 1.0,
      min_weight: parseFloat(getVal("weighMin")) || 0,
      auto_save: getBool("weighAutoSave"),
      auto_save_interval: parseFloat(getVal("weighAutoInterval")) || 2.0,
    },
    cloud: {
      provider: getVal("cloudProvider"),
      enabled: getBool("cloudEnabled"),
      format: getVal("cloudFormat"),
      filename: getVal("cloudFilename"),
      local_folder: getVal("cloudFolder"),
      webdav: {
        url: getVal("webdavUrl"),
        username: getVal("webdavUser"),
        password: getVal("webdavPass"),
      },
      onedrive_api: {
        tenant_id: getVal("odTenant"),
        client_id: getVal("odClientId"),
        client_secret: getVal("odClientSecret"),
        refresh_token: getVal("odRefreshToken"),
        folder: getVal("odFolder"),
      },
      gdrive_api: {
        credentials_file: getVal("gdCreds"),
        token_file: getVal("gdToken"),
        folder_id: getVal("gdFolderId"),
        folder: getVal("gdFolder"),
      },
    },
    server: {
      host: getVal("srvHost"),
      port: parseInt(getVal("srvPort")) || 5000,
    },
    ui: {
      company_name: getVal("uiCompany"),
      operator: getVal("uiOperator"),
    },
  };
}

/* ------------------------------ Eventos config -------------------------- */
$("connType").onchange = toggleConnFields;
$("cloudProvider").onchange = toggleCloudFields;
$("protoType").onchange = toggleRegexField;

/* Atalho: configura o sistema para o indicador Mettler Toledo TI400 via rede. */
const btnTi400 = $("btnTi400Preset");
if (btnTi400) {
  btnTi400.onclick = () => {
    // Protocolo TI400 (detecção automática P03 binário / P10 / P08).
    const sel = $("protoType");
    if (sel) sel.value = "ti400_auto";
    // Conexão pela rede (TCP/IP), que é como o TI400 transmite o P03.
    setVal("connType", "tcp");
    toggleConnFields();
    // Porta padrão do socket de rede do TI400 (Porta de comunicação A = 9000).
    const portEl = $("tcpPort");
    if (portEl && !portEl.value) portEl.value = "9000";
    // Abre a aba Conexão para informar IP e porta.
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
    const tab = document.querySelector('.tab[data-tab="tab-conn"]');
    if (tab) tab.classList.add("active");
    const panel = $("tab-conn");
    if (panel) panel.classList.add("active");
    const host = $("tcpHost");
    if (host) host.focus();
    toast("TI400 selecionado (porta 9000). Informe o IP e use \"Testar conexão de rede\".", "info");
  };
}

/* Testa a conectividade TCP/IP com a balança antes de salvar/conectar. */
const btnTestConn = $("btnTestConn");
if (btnTestConn) {
  btnTestConn.onclick = async () => {
    const out = $("testConnResult");
    const host = getVal("tcpHost");
    const port = parseInt(getVal("tcpPort")) || 9000;
    const timeout = parseFloat(getVal("tcpTimeout")) || 3.0;
    if (!host) {
      if (out) out.textContent = "Informe o IP/host da balança.";
      toast("Informe o IP da balança.", "error");
      return;
    }
    if (out) out.textContent = "Testando conexão...";
    const res = await api("/api/connection/test", "POST", { host, port, timeout });
    const msg = res.message || (res.ok ? "Conexão OK." : "Falha na conexão.");
    if (out) {
      out.textContent = msg;
      out.style.color = res.ok ? "#39d98a" : "#ff6b6b";
    }
    toast(msg, res.ok ? "info" : "error");
  };
}

$("btnSaveConfig").onclick = async () => {
  const res = await api("/api/config", "POST", collectConfig());
  if (res.ok) {
    fillConfig(res.config);
    toast("Configurações salvas. Reconectando...", "info");
  } else toast("Erro ao salvar", "error");
};

$("btnResetConfig").onclick = async () => {
  if (!confirm("Restaurar todas as configurações padrão?")) return;
  const res = await api("/api/config/reset", "POST");
  if (res.ok) { fillConfig(res.config); toast("Configurações restauradas", "warning"); }
};

$("btnScanPorts").onclick = async () => {
  const res = await api("/api/ports");
  const ports = res.ports || [];
  if (!ports.length) { $("portsHint").textContent = "Nenhuma porta serial detectada."; return toast("Nenhuma porta detectada", "warning"); }
  $("portsHint").innerHTML = "Detectadas: " + ports.map((p) =>
    `<a href="#" onclick="document.getElementById('serialPort').value='${p.device}';return false;">${p.device}</a>`).join(", ");
  toast(`${ports.length} porta(s) detectada(s)`, "info");
};

$("btnTestCloud").onclick = async () => {
  toast("Testando destino...", "info");
  const res = await api("/api/cloud/test", "POST");
  if (res.ok) toast("✅ Destino OK! Pesagem de teste enviada.", "info");
  else toast("❌ Falha: " + (res.error || "erro desconhecido"), "error");
};

/* ------------------------------ Init ------------------------------------ */
async function init() {
  try {
    const cfg = await api("/api/config");
    if (cfg.ok) fillConfig(cfg.config);

    const proto = await api("/api/protocols");
    if (proto.ok) {
      const sel = $("protoType");
      sel.innerHTML = proto.protocols.map((p) => `<option value="${p.id}">${p.name}</option>`).join("");
      if (config) sel.value = config.protocol.type;
    }

    const st = await api("/api/status");
    if (st.ok) {
      renderState(st.state);
      updateCloudStats(st.cloud);
    }

    const hist = await api("/api/history");
    if (hist.ok) { history = hist.history || []; renderHistory(); }

    const logs = await api("/api/logs");
    if (logs.ok) (logs.logs || []).forEach(appendLog);
  } catch (e) {
    toast("Erro ao inicializar: " + e.message, "error");
  }
}

function updateCloudStats(cloud) {
  if (!cloud) return;
  $("statSaved").textContent = cloud.saved ?? 0;
  $("statFailed").textContent = cloud.failed ?? 0;
  $("statQueued").textContent = cloud.queued ?? 0;
}

// Atualiza estatísticas periodicamente.
setInterval(async () => {
  try {
    const st = await api("/api/status");
    if (st.ok) updateCloudStats(st.cloud);
  } catch (e) { /* ignora */ }
}, 5000);

init();
