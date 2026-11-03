// ============================================================
// Estado global simples
// ============================================================
let map;
let markers = {}; // line_id -> L.Marker
let routesDrawn = false; // as rotas só precisam ser desenhadas uma vez
let selectedLine = null;
let selectedStatus = null;
let userCoords = null;
let selectedAmount = 10;

const STATUS_ORDER = { atrasado: 0, quebrado: 0, no_horario: 1 };

// --- Rotas (desenho + modo de edição) ---
const routeState = {}; // line_id -> { line, wp, stops, snapped, original, layers }
let editing = false;
const OSRM_URL = "https://router.project-osrm.org/route/v1/driving/";
const SAVE_PREFIX = "ondetobus_rota_v1_";
const EDIT_HINT =
  "Arraste os pontos brancos até as ruas certas. Clique na linha para criar um ponto novo. Toque num ponto para apagá-lo.";

// ============================================================
// Inicialização
// ============================================================
document.addEventListener("DOMContentLoaded", async () => {
  initMap();
  await loadLines();
  await refreshBoard();
  setInterval(refreshBoard, 8000);
  tickClock();
  setInterval(tickClock, 1000);
  bindModals();
  await loadCard();
});

function initMap() {
  // Todas as interações ligadas de forma explícita: arrastar, zoom com
  // roda do mouse, pinça no celular, duplo clique e teclado.
  map = L.map("map", {
    zoomControl: true,
    dragging: true,
    touchZoom: true,
    scrollWheelZoom: true,
    doubleClickZoom: true,
    boxZoom: true,
    keyboard: true,
    tap: true,
    inertia: true,
  }).setView([-30.0392, -52.8939], 14);

  // Mapa "Voyager" (CARTO): mostra bem as ruas e os nomes.
  L.tileLayer("https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png", {
    maxZoom: 19,
    subdomains: "abcd",
    attribution: "&copy; OpenStreetMap contributors &copy; CARTO",
  }).addTo(map);

  // Garante que o Leaflet conheça o tamanho real do mapa (sem isso o mapa
  // pode parecer "travado", como uma imagem).
  setTimeout(() => map.invalidateSize(), 0);
  setTimeout(() => map.invalidateSize(), 500);
  window.addEventListener("resize", () => map.invalidateSize());

  addEditControl();
}

function tickClock() {
  const el = document.getElementById("clock");
  const now = new Date();
  el.textContent = now.toLocaleTimeString("pt-BR", { hour12: false });
}

// ============================================================
// Linhas + painel de horários
// ============================================================
async function loadLines() {
  const res = await fetch("/api/lines");
  const lines = await res.json();

  const chipRow = document.getElementById("line-chips");
  chipRow.innerHTML = "";
  lines.forEach((line) => {
    const chip = document.createElement("button");
    chip.className = "chip";
    chip.textContent = line.name;
    chip.style.borderColor = line.color;
    chip.addEventListener("click", () => {
      selectedLine = line.id;
      [...chipRow.children].forEach((c) => c.classList.remove("is-selected"));
      chip.classList.add("is-selected");
      validateReportForm();
    });
    chipRow.appendChild(chip);
  });
}

async function refreshBoard() {
  const res = await fetch("/api/status");
  const data = await res.json();

  if (!routesDrawn) {
    drawRoutes(data);
    routesDrawn = true;
  }

  data.sort((a, b) => (STATUS_ORDER[a.status] ?? 1) - (STATUS_ORDER[b.status] ?? 1));

  const list = document.getElementById("board-list");
  list.innerHTML = "";

  data.forEach((line) => {
    const statusKey = line.status || "sem_relatos";
    const row = document.createElement("div");
    row.className = "board__row board__row--flicker";

    const timeLabel = line.seconds_ago == null ? "—" : formatAgo(line.seconds_ago);

    row.innerHTML = `
      <span class="board__dot" style="background:${line.color}"></span>
      <div>
        <p class="board__name">${line.name}</p>
        <p class="board__meta">${line.reporters} relato(s) recente(s) · há ${timeLabel}</p>
      </div>
      <span class="board__status board__status--${statusKey}">${line.status_label}</span>
    `;
    list.appendChild(row);

    updateMarker(line);
  });
}

// ============================================================
// Rotas: desenho
// ============================================================
function loadSaved(id) {
  try {
    const raw = localStorage.getItem(SAVE_PREFIX + id);
    return raw ? JSON.parse(raw) : null;
  } catch (e) {
    return null;
  }
}

function saveRoute(id, data) {
  try {
    localStorage.setItem(SAVE_PREFIX + id, JSON.stringify(data));
    return true;
  } catch (e) {
    return false;
  }
}

function clearSaved(id) {
  try {
    localStorage.removeItem(SAVE_PREFIX + id);
  } catch (e) {
    /* sem armazenamento: tudo bem */
  }
}

function drawRoutes(lines) {
  lines.forEach((line) => {
    const stops = (line.stops || []).map((s) => ({ ...s }));
    const baseWp =
      line.waypoints && line.waypoints.length >= 2
        ? line.waypoints.map((p) => [p[0], p[1]])
        : stops.map((s) => [s.lat, s.lng]);
    const snapped = line.path && line.path.length >= 2 ? line.path : baseWp;
    if (baseWp.length < 2) return;

    const saved = loadSaved(line.id);
    routeState[line.id] = {
      line,
      wp: saved && saved.wp ? saved.wp : baseWp.map((p) => [...p]),
      stops: saved && saved.stops ? saved.stops : stops,
      snapped: saved && saved.snapped ? saved.snapped : snapped,
      original: {
        wp: baseWp.map((p) => [...p]),
        stops: stops.map((s) => ({ ...s })),
        snapped,
      },
      layers: { outline: null, main: null, stopMarkers: [], editMarkers: [] },
      timer: null,
    };
    renderRoute(routeState[line.id]);
  });
  fitToRoutes();
}

function fitToRoutes() {
  const bounds = [];
  Object.values(routeState).forEach((st) => st.snapped.forEach((p) => bounds.push(p)));
  map.invalidateSize();
  if (bounds.length) map.fitBounds(bounds, { padding: [40, 40] });
}

function renderRoute(st) {
  const ly = st.layers;
  [ly.outline, ly.main].forEach((l) => l && map.removeLayer(l));
  ly.stopMarkers.forEach((m) => map.removeLayer(m));
  ly.editMarkers.forEach((m) => map.removeLayer(m));
  ly.stopMarkers = [];
  ly.editMarkers = [];

  // contorno escuro por baixo + linha principal por cima (efeito "estrada")
  ly.outline = L.polyline(st.snapped, {
    color: "#0b3d24",
    weight: 10,
    opacity: 0.9,
    lineJoin: "round",
    lineCap: "round",
    interactive: false,
  }).addTo(map);
  ly.main = L.polyline(st.snapped, {
    color: st.line.color,
    weight: 6,
    opacity: 1,
    lineJoin: "round",
    lineCap: "round",
  }).addTo(map);
  ly.main.bindTooltip(st.line.name, { sticky: true });
  ly.main.on("click", (e) => {
    if (!editing) return;
    L.DomEvent.stopPropagation(e);
    insertWaypoint(st, e.latlng);
  });

  // pontos com nome (UFSM, Stok Center, ...)
  st.stops.forEach((stop, i) => {
    let m;
    if (editing) {
      const icon = L.divIcon({
        className: "",
        html: `<div style="width:20px;height:20px;border-radius:50%;background:#0b3d24;border:3px solid #fff;box-shadow:0 0 0 2px rgba(0,0,0,.5);"></div>`,
        iconSize: [20, 20],
        iconAnchor: [10, 10],
      });
      m = L.marker([stop.lat, stop.lng], { icon, draggable: true, zIndexOffset: 800 }).addTo(map);
      m.on("dragend", () => {
        const p = m.getLatLng();
        st.stops[i].lat = p.lat;
        st.stops[i].lng = p.lng;
      });
    } else {
      m = L.circleMarker([stop.lat, stop.lng], {
        radius: 7,
        color: "#ffffff",
        weight: 2,
        fillColor: "#0b3d24",
        fillOpacity: 1,
      }).addTo(map);
    }
    m.bindTooltip(stop.name, { permanent: true, direction: "right", offset: [10, 0] });
    ly.stopMarkers.push(m);
  });

  // pontos brancos do traçado (só no modo de edição)
  if (editing) {
    st.wp.forEach((_, i) => {
      const icon = L.divIcon({
        className: "",
        html: `<div style="width:16px;height:16px;border-radius:50%;background:#fff;border:3px solid ${st.line.color};box-shadow:0 0 0 2px rgba(0,0,0,.45);"></div>`,
        iconSize: [16, 16],
        iconAnchor: [8, 8],
      });
      const m = L.marker(st.wp[i], { icon, draggable: true, zIndexOffset: 500 }).addTo(map);
      m.on("dragend", () => {
        const p = m.getLatLng();
        st.wp[i] = [p.lat, p.lng];
        scheduleResnap(st);
      });
      m.bindPopup(() => {
        const box = document.createElement("div");
        box.innerHTML = `<strong>Ponto ${i + 1}</strong><br>`;
        const btn = document.createElement("button");
        btn.type = "button";
        btn.textContent = "Apagar este ponto";
        btn.addEventListener("click", () => {
          if (st.wp.length > 2) {
            st.wp.splice(i, 1);
            map.closePopup();
            renderRoute(st);
            scheduleResnap(st);
          }
        });
        box.appendChild(btn);
        return box;
      });
      ly.editMarkers.push(m);
    });
  }
}

// Cria um ponto novo onde o usuário clicou na linha, na posição certa
// da sequência.
function insertWaypoint(st, latlng) {
  const snapped = st.snapped;
  let k = 0;
  let kd = Infinity;
  snapped.forEach((p, i) => {
    const d = map.distance(p, latlng);
    if (d < kd) {
      kd = d;
      k = i;
    }
  });

  // posição de cada ponto da rota dentro da linha (sempre crescente)
  let last = -1;
  let from = 0;
  for (let i = 0; i < st.wp.length; i++) {
    let idx = from;
    let dmin = Infinity;
    for (let j = from; j < snapped.length; j++) {
      const d = map.distance(snapped[j], st.wp[i]);
      if (d < dmin) {
        dmin = d;
        idx = j;
      }
    }
    if (idx <= k) last = i;
    from = idx;
  }

  const at = Math.max(0, Math.min(st.wp.length - 1, last));
  st.wp.splice(at + 1, 0, [latlng.lat, latlng.lng]);
  renderRoute(st);
  scheduleResnap(st);
}

// ============================================================
// Rotas: encaixe nas ruas (OSRM, direto do navegador)
// ============================================================
function scheduleResnap(st) {
  clearTimeout(st.timer);
  setHint("Ajustando às ruas…");
  st.timer = setTimeout(() => resnap(st), 450);
}

async function resnap(st) {
  const coords = st.wp.map((p) => `${p[1].toFixed(6)},${p[0].toFixed(6)}`).join(";");
  const url = `${OSRM_URL}${coords}?overview=full&geometries=geojson&continue_straight=true`;
  try {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 9000);
    const res = await fetch(url, { signal: ctrl.signal });
    clearTimeout(t);
    const data = await res.json();
    if (data.code === "Ok" && data.routes && data.routes[0]) {
      st.snapped = data.routes[0].geometry.coordinates.map((c) => [c[1], c[0]]);
      setHint(EDIT_HINT);
    } else {
      throw new Error(data.code || "erro");
    }
  } catch (e) {
    st.snapped = st.wp.map((p) => [...p]);
    setHint("Sem acesso ao serviço de ruas agora. Mostrando linha reta provisória.");
  }
  st.layers.outline.setLatLngs(st.snapped);
  st.layers.main.setLatLngs(st.snapped);
}

// ============================================================
// Rotas: controle de edição (botões no canto do mapa)
// ============================================================
function setHint(text) {
  const el = document.getElementById("rt-hint");
  if (el) el.textContent = text;
}

function styleBtn(btn) {
  btn.type = "button";
  btn.style.cssText =
    "cursor:pointer;border:1px solid #b9b9b9;border-radius:6px;background:#f7f7f7;padding:7px 9px;font:600 13px system-ui,sans-serif;text-align:left;color:#14161b;";
}

function addEditControl() {
  const Ctl = L.Control.extend({
    options: { position: "topright" },
    onAdd() {
      const box = L.DomUtil.create("div", "leaflet-bar");
      box.style.cssText =
        "background:#fff;padding:6px;display:flex;flex-direction:column;gap:6px;max-width:190px;font:13px system-ui,sans-serif;";
      box.innerHTML = `
        <button id="rt-edit">✏️ Editar rota</button>
        <div id="rt-tools" style="display:none;flex-direction:column;gap:6px;">
          <button id="rt-save">💾 Salvar neste aparelho</button>
          <button id="rt-copy">📋 Copiar coordenadas</button>
          <button id="rt-reset">↩️ Voltar ao original</button>
          <div id="rt-hint" style="font-size:12px;color:#333;line-height:1.3;"></div>
        </div>`;
      L.DomEvent.disableClickPropagation(box);
      L.DomEvent.disableScrollPropagation(box);
      return box;
    },
  });
  map.addControl(new Ctl());

  ["rt-edit", "rt-save", "rt-copy", "rt-reset"].forEach((id) => styleBtn(document.getElementById(id)));
  document.getElementById("rt-edit").addEventListener("click", () => setEditing(!editing));
  document.getElementById("rt-save").addEventListener("click", saveAll);
  document.getElementById("rt-copy").addEventListener("click", copyCoords);
  document.getElementById("rt-reset").addEventListener("click", resetAll);
}

function setEditing(on) {
  editing = on;
  map.closePopup();
  document.getElementById("rt-edit").textContent = on ? "✅ Terminar edição" : "✏️ Editar rota";
  document.getElementById("rt-tools").style.display = on ? "flex" : "none";
  setHint(EDIT_HINT);
  Object.values(routeState).forEach(renderRoute);
}

function firstRoute() {
  return Object.values(routeState)[0];
}

function saveAll() {
  const st = firstRoute();
  if (!st) return;
  const ok = saveRoute(st.line.id, { wp: st.wp, stops: st.stops, snapped: st.snapped });
  setHint(ok ? "Salvo neste aparelho ✓ (para valer para todos, use “Copiar coordenadas”)." : "Não foi possível salvar aqui.");
}

function resetAll() {
  const st = firstRoute();
  if (!st) return;
  clearSaved(st.line.id);
  st.wp = st.original.wp.map((p) => [...p]);
  st.stops = st.original.stops.map((s) => ({ ...s }));
  st.snapped = st.original.snapped;
  renderRoute(st);
  setHint("Voltou ao traçado original.");
}

function buildPythonText(st) {
  const f = (n) => Number(n).toFixed(6);
  const stops = st.stops
    .map((s) => `            {"name": ${JSON.stringify(s.name)}, "lat": ${f(s.lat)}, "lng": ${f(s.lng)}},`)
    .join("\n");
  const wp = st.wp.map((p) => `            [${f(p[0])}, ${f(p[1])}],`).join("\n");
  return `        "stops": [\n${stops}\n        ],\n        "path": [\n${wp}\n        ],`;
}

function copyCoords() {
  const st = firstRoute();
  if (!st) return;
  const text = buildPythonText(st);

  const overlay = document.createElement("div");
  overlay.style.cssText =
    "position:fixed;inset:0;z-index:10000;background:rgba(0,0,0,.6);display:flex;align-items:center;justify-content:center;padding:16px;";
  const card = document.createElement("div");
  card.style.cssText =
    "background:#fff;border-radius:10px;padding:14px;width:min(640px,100%);max-height:90vh;display:flex;flex-direction:column;gap:10px;font:14px system-ui,sans-serif;color:#14161b;";
  const title = document.createElement("strong");
  title.textContent = "Coordenadas da rota (cole no ondetobus.py, ou me envie)";
  const area = document.createElement("textarea");
  area.readOnly = true;
  area.value = text;
  area.style.cssText = "width:100%;height:50vh;font:12px ui-monospace,monospace;";
  const row = document.createElement("div");
  row.style.cssText = "display:flex;gap:8px;justify-content:flex-end;";
  const copyBtn = document.createElement("button");
  const closeBtn = document.createElement("button");
  copyBtn.textContent = "Copiar";
  closeBtn.textContent = "Fechar";
  [copyBtn, closeBtn].forEach(styleBtn);
  copyBtn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(text);
      copyBtn.textContent = "Copiado ✓";
    } catch (e) {
      area.select();
      document.execCommand("copy");
      copyBtn.textContent = "Copiado ✓";
    }
  });
  closeBtn.addEventListener("click", () => overlay.remove());
  row.append(copyBtn, closeBtn);
  card.append(title, area, row);
  overlay.appendChild(card);
  document.body.appendChild(overlay);
  area.focus();
  area.select();
}

// ============================================================
// Marcador do ônibus
// ============================================================
function formatAgo(seconds) {
  if (seconds < 60) return `${seconds}s`;
  const min = Math.floor(seconds / 60);
  if (min < 60) return `${min} min`;
  return `${Math.floor(min / 60)} h`;
}

function updateMarker(line) {
  if (line.lat == null || line.lng == null) return;

  const icon = L.divIcon({
    className: "",
    html: `<div style="
      width:16px;height:16px;border-radius:50%;
      background:${line.color};border:2px solid #14161b;
      box-shadow:0 0 0 3px rgba(255,255,255,0.6);
    "></div>`,
    iconSize: [16, 16],
  });

  if (markers[line.id]) {
    markers[line.id].setLatLng([line.lat, line.lng]);
  } else {
    markers[line.id] = L.marker([line.lat, line.lng], { icon }).addTo(map);
  }
  markers[line.id].bindPopup(
    `<strong>${line.name}</strong><br>${line.status_label} · há ${formatAgo(line.seconds_ago || 0)}`
  );
}

// ============================================================
// Modais
// ============================================================
function bindModals() {
  document.getElementById("open-report").addEventListener("click", () => openReportModal());
  document.getElementById("open-recharge").addEventListener("click", () => toggleModal("recharge-overlay", true));

  document.querySelectorAll("[data-close]").forEach((btn) => {
    btn.addEventListener("click", () => toggleModal(btn.dataset.close, false));
  });

  document.querySelectorAll(".chip--status").forEach((chip) => {
    chip.addEventListener("click", () => {
      selectedStatus = chip.dataset.status;
      document.querySelectorAll(".chip--status").forEach((c) => c.classList.remove("is-selected"));
      chip.classList.add("is-selected");
      validateReportForm();
    });
  });

  document.querySelectorAll("#amount-chips .chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      selectedAmount = Number(chip.dataset.amount);
      document.querySelectorAll("#amount-chips .chip").forEach((c) => c.classList.remove("is-selected"));
      chip.classList.add("is-selected");
    });
  });
  document.querySelector("#amount-chips .chip").classList.add("is-selected");

  document.getElementById("submit-report").addEventListener("click", submitReport);
  document.getElementById("submit-recharge").addEventListener("click", submitRecharge);
}

function toggleModal(id, open) {
  document.getElementById(id).classList.toggle("is-open", open);
}

function openReportModal() {
  toggleModal("report-overlay", true);
  const geoStatus = document.getElementById("geo-status");
  geoStatus.textContent = "Buscando sua localização…";

  if (!navigator.geolocation) {
    geoStatus.textContent = "Seu navegador não suporta geolocalização.";
    return;
  }

  navigator.geolocation.getCurrentPosition(
    (pos) => {
      userCoords = { lat: pos.coords.latitude, lng: pos.coords.longitude };
      geoStatus.textContent = "Localização encontrada ✓";
      validateReportForm();
    },
    () => {
      geoStatus.textContent = "Não foi possível obter sua localização. Verifique a permissão do navegador.";
    }
  );
}

function validateReportForm() {
  const btn = document.getElementById("submit-report");
  btn.disabled = !(selectedLine && selectedStatus && userCoords);
}

async function submitReport() {
  const btn = document.getElementById("submit-report");
  btn.disabled = true;
  btn.textContent = "Enviando…";

  await fetch("/api/report", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      line_id: selectedLine,
      status: selectedStatus,
      lat: userCoords.lat,
      lng: userCoords.lng,
    }),
  });

  btn.textContent = "Relato enviado ✓";
  await refreshBoard();
  setTimeout(() => {
    toggleModal("report-overlay", false);
    btn.textContent = "Enviar relato";
    validateReportForm();
  }, 900);
}

// ============================================================
// Recarga simulada
// ============================================================
async function loadCard() {
  const res = await fetch("/api/card?number=" + encodeURIComponent("**** 4821"));
  const data = await res.json();
  document.getElementById("card-balance").textContent = formatMoney(data.balance);
}

async function submitRecharge() {
  const resultEl = document.getElementById("recharge-result");
  resultEl.textContent = "Processando recarga simulada…";
  resultEl.className = "modal__result";

  const res = await fetch("/api/recharge", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ card_number: "**** 4821", amount: selectedAmount }),
  });
  const data = await res.json();

  if (data.error) {
    resultEl.textContent = data.error;
    resultEl.className = "modal__result is-error";
    return;
  }

  resultEl.textContent = `Recarga simulada de ${formatMoney(data.amount)} concluída. Novo saldo: ${formatMoney(data.new_balance)}.`;
  resultEl.className = "modal__result is-success";
  document.getElementById("card-balance").textContent = formatMoney(data.new_balance);
}

function formatMoney(value) {
  return value.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
}
