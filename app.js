// ============================================================
// Estado global simples
// ============================================================
let map;
let markers = {}; // line_id -> L.Marker
let routesDrawn = false;
let routesSnapped = false;
let routeLayer; // as rotas (polylines) só precisam ser desenhadas uma vez
let selectedLine = null;
let selectedStatus = null;
let userCoords = null;
let selectedAmount = 10;
let linesData = []; // linhas + tabela de horários (/api/lines)
let routeGeo = {}; // line_id -> { path, cum, stopFracs } usado pela simulação
let simMarkers = {}; // "linha|viagem" -> L.Marker (ônibus simulado)

const STATUS_ORDER = { atrasado: 0, quebrado: 0, no_horario: 1 };

// ============================================================
// Inicialização
// ============================================================
document.addEventListener("DOMContentLoaded", async () => {
  initMap();
  bindModals();
  tickClock();
  setInterval(tickClock, 1000);
  try { await loadLines(); } catch (e) { console.error(e); }
  try { await setupSimulation(); } catch (e) { console.error(e); }
  try { await refreshBoard(); } catch (e) { console.error(e); }
  setInterval(() => refreshBoard().catch(console.error), 8000);
  try { await loadCard(); } catch (e) { console.error(e); }
});

function initMap() {
  map = L.map("map", { zoomControl: true }).setView([-30.0392, -52.8939], 14);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap",
  }).addTo(map);
  routeLayer = L.layerGroup().addTo(map);
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
  linesData = lines;

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
    drawStops(data);
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
        <p class="board__meta" id="next-stop-${line.id}"></p>
      </div>
      <span class="board__status board__status--${statusKey}">${line.status_label}</span>
    `;
    list.appendChild(row);

    updateMarker(line);
  });
}

// Paradas (pontos oficiais extraídos dos horários da TNSG). A linha do
// trajeto em si é desenhada por setupSimulation() (já encaixada nas ruas).
function drawStops(lines) {
  lines.forEach((line) => {
    (line.stops || []).forEach((stop) => {
      L.circleMarker([stop.lat, stop.lng], {
        radius: 6,
        color: "#ffffff",
        weight: 2,
        fillColor: line.color,
        fillOpacity: 1,
      })
        .bindTooltip(stop.name, { direction: "top" })
        .addTo(routeLayer);
    });
  });
}

function formatAgo(seconds) {
  if (seconds < 60) return `${seconds}s`;
  const min = Math.floor(seconds / 60);
  if (min < 60) return `${min} min`;
  return `${Math.floor(min / 60)} h`;
}

function updateMarker(line) {
  // Enquanto há viagem da tabela em andamento, quem aparece no mapa é o
  // ônibus simulado (abaixo), não a bolinha do último relato.
  if (line.simulated) {
    if (markers[line.id]) {
      map.removeLayer(markers[line.id]);
      delete markers[line.id];
    }
    return;
  }
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

// ============================================================
// Simulação dos ônibus pela tabela de horários
// (dias úteis, sentido UFSM -> HCB; animação de 1 em 1 segundo)
//
// Opções na URL (para testar fora do horário / no fim de semana):
//   ?t=14:07   finge que são 14:07 de um dia útil
//   ?demo=1    um ônibus faz a viagem inteira a cada 60 segundos
// ============================================================
const QS = new URLSearchParams(location.search);
const FORCED_TIME = QS.get("t");
const DEMO = QS.get("demo") === "1";
const M_PER_DEG = 111320;

function geoDist(a, b) {
  return Math.hypot(
    (b[0] - a[0]) * M_PER_DEG,
    (b[1] - a[1]) * M_PER_DEG * Math.cos((a[0] * Math.PI) / 180)
  );
}

function pathLength(p) {
  let t = 0;
  for (let i = 1; i < p.length; i++) t += geoDist(p[i - 1], p[i]);
  return t;
}

function densify(p, step) {
  const out = [p[0]];
  for (let i = 1; i < p.length; i++) {
    const n = Math.max(1, Math.round(geoDist(p[i - 1], p[i]) / step));
    for (let k = 1; k <= n; k++) {
      out.push([
        p[i - 1][0] + ((p[i][0] - p[i - 1][0]) * k) / n,
        p[i - 1][1] + ((p[i][1] - p[i - 1][1]) * k) / n,
      ]);
    }
  }
  return out;
}

// Encaixa o traçado nas ruas com o OSRM (gratuito, dados do OpenStreetMap).
// Feito aqui no navegador e guardado no localStorage: só chama uma vez.
// Se falhar, devolve null e o trajeto original (extraído da imagem) é usado.
async function snapToStreets(raw) {
  const key = "ondetobus-snap-v2-" + JSON.stringify(raw).length;
  try {
    const cached = localStorage.getItem(key);
    if (cached) return JSON.parse(cached);
  } catch (e) {}

  let step = Math.max(40, pathLength(raw) / 90);
  let pts = densify(raw, step);
  while (pts.length > 100) {
    step *= 1.15;
    pts = densify(raw, step);
  }
  const coords = pts.map((p) => p[1].toFixed(5) + "," + p[0].toFixed(5)).join(";");
  const radii = pts.map(() => 45).join(";");
  const url =
    "https://router.project-osrm.org/match/v1/driving/" + coords +
    "?geometries=geojson&overview=full&gaps=ignore&radiuses=" + radii;
  try {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), 12000);
    const res = await fetch(url, { signal: ctl.signal });
    clearTimeout(timer);
    const j = await res.json();
    if (j.code !== "Ok" || !j.matchings || !j.matchings.length) return null;
    const out = [];
    j.matchings.forEach((m) => m.geometry.coordinates.forEach((c) => out.push([c[1], c[0]])));
    const ratio = pathLength(out) / pathLength(raw);
    if (ratio < 0.85 || ratio > 1.3) return null; // resultado estranho: descarta
    try { localStorage.setItem(key, JSON.stringify(out)); } catch (e) {}
    return out;
  } catch (e) {
    return null;
  }
}

function buildGeo(line, path) {
  const cum = [0];
  for (let i = 1; i < path.length; i++) cum.push(cum[i - 1] + geoDist(path[i - 1], path[i]));
  // fração do trajeto (0..1) em que cada parada fica, sempre avançando
  const total = cum[cum.length - 1];
  const stopFracs = [];
  let fromSeg = 0;
  (line.stops || []).forEach((stop) => {
    const k = Math.cos((stop.lat * Math.PI) / 180);
    let best = { d: Infinity, seg: fromSeg, along: 0 };
    for (let i = fromSeg; i < path.length - 1; i++) {
      const ax = path[i][1] * k * M_PER_DEG, ay = path[i][0] * M_PER_DEG;
      const bx = path[i + 1][1] * k * M_PER_DEG, by = path[i + 1][0] * M_PER_DEG;
      const px = stop.lng * k * M_PER_DEG, py = stop.lat * M_PER_DEG;
      const vx = bx - ax, vy = by - ay;
      const len2 = vx * vx + vy * vy || 1;
      const t = Math.max(0, Math.min(1, ((px - ax) * vx + (py - ay) * vy) / len2));
      const d = Math.hypot(px - (ax + vx * t), py - (ay + vy * t));
      if (d < best.d) best = { d, seg: i, along: cum[i] + t * (cum[i + 1] - cum[i]) };
    }
    fromSeg = best.seg;
    stopFracs.push(total ? best.along / total : 0);
  });
  if (stopFracs.length) { stopFracs[0] = 0; stopFracs[stopFracs.length - 1] = 1; }
  return { path, cum, stopFracs };
}

function pointAt(geo, frac) {
  const { path, cum } = geo;
  const t = cum[cum.length - 1] * Math.min(1, Math.max(0, frac));
  let lo = 0, hi = cum.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (cum[mid] <= t) lo = mid; else hi = mid;
  }
  const f = (t - cum[lo]) / (cum[hi] - cum[lo] || 1);
  return [
    path[lo][0] + (path[hi][0] - path[lo][0]) * f,
    path[lo][1] + (path[hi][1] - path[lo][1]) * f,
  ];
}

function busIcon(color) {
  const svg = `<svg width="40" height="40" viewBox="0 0 48 48" xmlns="http://www.w3.org/2000/svg"
      style="filter:drop-shadow(0 2px 3px rgba(0,0,0,.5))">
    <circle cx="24" cy="24" r="22" fill="${color}" stroke="#fff" stroke-width="3"/>
    <rect x="11" y="12" width="26" height="22" rx="5" fill="#fff"/>
    <rect x="14" y="15" width="20" height="8" rx="2" fill="${color}"/>
    <rect x="14" y="26" width="5" height="3" rx="1" fill="#F5A623"/>
    <rect x="29" y="26" width="5" height="3" rx="1" fill="#F5A623"/>
    <circle cx="17" cy="35" r="3" fill="#222"/><circle cx="31" cy="35" r="3" fill="#222"/></svg>`;
  return L.divIcon({ html: svg, className: "", iconSize: [40, 40], iconAnchor: [20, 20] });
}

async function setupSimulation() {
  const polylines = {};
  linesData.forEach((line) => {
    const raw = line.path || [];
    if (raw.length < 2) return;
    routeGeo[line.id] = buildGeo(line, raw);
    polylines[line.id] = [
      L.polyline(raw, { color: "#ffffff", weight: 9, opacity: 0.9 }).addTo(routeLayer),
      L.polyline(raw, { color: line.color, weight: 5, opacity: 0.95 }).addTo(routeLayer),
    ];
  });
  const first = Object.values(polylines)[0];
  if (first) map.fitBounds(first[1].getBounds(), { padding: [30, 30] });

  tickSimulation();
  setInterval(tickSimulation, 1000);

  // em segundo plano: troca o traçado pelo encaixado nas ruas
  for (const line of linesData) {
    if (!routeGeo[line.id] || line.snapped) continue; // servidor já encaixou nas ruas
    const snapped = await snapToStreets(line.path);
    if (snapped) {
      routeGeo[line.id] = buildGeo(line, snapped);
      polylines[line.id].forEach((pl) => pl.setLatLngs(snapped));
    }
  }
}

const DEMO_TRIP = [[600, 640, "Demonstração"]];
function demoMinute() {
  return 600 + 40 * ((Date.now() % 60000) / 60000);
}

function brNow() {
  if (DEMO) return { m: demoMinute(), weekday: true, demo: true };
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-US", {
      timeZone: "America/Sao_Paulo", weekday: "short", hour: "2-digit",
      minute: "2-digit", second: "2-digit", hour12: false,
    }).formatToParts(new Date()).map((p) => [p.type, p.value])
  );
  let m = (Number(parts.hour) % 24) * 60 + Number(parts.minute) + Number(parts.second) / 60;
  let weekday = !["Sat", "Sun"].includes(parts.weekday);
  if (FORCED_TIME) {
    const [h, mi] = FORCED_TIME.split(":");
    m = Number(h) * 60 + Number(mi) + Number(parts.second) / 60;
    weekday = true;
  }
  return { m, weekday };
}

function fmtMin(m) {
  m = Math.floor(m + 1e-6);
  return Math.floor(m / 60) + ":" + String(m % 60).padStart(2, "0");
}

function tickSimulation() {
  const { m, weekday, demo } = brNow();
  const alive = new Set();

  linesData.forEach((line) => {
    const geo = routeGeo[line.id];
    if (!geo) return;
    let mm = m;
    let trips = demo ? DEMO_TRIP : weekday ? line.timetable || [] : [];
    let active = trips.filter(([dep, arr]) => mm >= dep && mm <= arr);
    // Fora do horário das viagens (noite / fim de semana): mostra uma
    // viagem de demonstração em loop, para o ônibus sempre aparecer andando.
    const fake = !active.length && !demo && !FORCED_TIME;
    const realNext = fake ? (weekday ? trips.find((t) => t[0] > m) : null) : null;
    if (fake) {
      trips = DEMO_TRIP;
      active = DEMO_TRIP;
      mm = demoMinute();
    }

    active.forEach(([dep, arr, tag]) => {
      const key = line.id + "|" + dep;
      alive.add(key);
      const frac = (mm - dep) / (arr - dep);
      const pos = pointAt(geo, frac);
      const next = (line.stops || []).findIndex((_, i) => geo.stopFracs[i] > frac + 1e-6);
      const nextTxt = next >= 0
        ? `Próxima parada: ${line.stops[next].name} · ${fmtMin(dep + geo.stopFracs[next] * (arr - dep))}`
        : "Chegando ao destino";
      const label = `<strong>Linha ${line.id}</strong> · ${fake ? "demonstração" : tag}<br>Saiu ${fmtMin(dep)} → chega ${fmtMin(arr)}<br>${nextTxt}`;
      if (!simMarkers[key]) {
        simMarkers[key] = L.marker(pos, { icon: busIcon(line.color), zIndexOffset: 1000 })
          .addTo(map)
          .bindPopup(label);
      } else {
        simMarkers[key].setLatLng(pos);
        simMarkers[key].getPopup().setContent(label);
      }
    });

    const el = document.getElementById("next-stop-" + line.id);
    if (el) {
      if (active.length) {
        const [dep, arr] = active[0];
        const frac = (mm - dep) / (arr - dep);
        const next = (line.stops || []).findIndex((_, i) => geo.stopFracs[i] > frac + 1e-6);
        const txt = next >= 0
          ? `🚌 Próxima parada: ${line.stops[next].name} · ${fmtMin(dep + geo.stopFracs[next] * (arr - dep))}`
          : "🚌 Chegando ao destino";
        el.textContent = fake
          ? `🎬 Demonstração (sem viagens agora${realNext ? ", próxima saída real " + fmtMin(realNext[0]) : ""}) · ${txt}`
          : txt;
      } else {
        const nx = weekday ? trips.find((t) => t[0] > mm) : null;
        el.textContent = nx
          ? `Próxima saída da UFSM: ${fmtMin(nx[0])}`
          : weekday ? "Sem mais viagens hoje" : "Sem viagens no fim de semana (tabela de dias úteis)";
      }
    }
  });

  Object.keys(simMarkers).forEach((k) => {
    if (!alive.has(k)) {
      map.removeLayer(simMarkers[k]);
      delete simMarkers[k];
    }
  });
}
