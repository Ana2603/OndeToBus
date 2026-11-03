// ============================================================
// Estado global simples
// ============================================================
let map;
let markers = {}; // line_id -> L.Marker
let routesDrawn = false; // as rotas (polylines) só precisam ser desenhadas uma vez
let selectedLine = null;
let selectedStatus = null;
let userCoords = null;
let selectedAmount = 10;

const STATUS_ORDER = { atrasado: 0, quebrado: 0, no_horario: 1 };

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
  map = L.map("map", { zoomControl: true }).setView([-30.0392, -52.8939], 14);
  // Mapa "Voyager" (CARTO): mostra bem as ruas e os nomes, bem mais legível
  // que o mapa padrão quando damos zoom na rota.
  L.tileLayer("https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png", {
    maxZoom: 19,
    subdomains: "abcd",
    attribution: "&copy; OpenStreetMap contributors &copy; CARTO",
  }).addTo(map);
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

// Desenha o traçado de cada linha como uma linha SÓLIDA que segue as ruas.
// Usa "path" (traçado já encaixado nas ruas pelo servidor); se a linha não
// tiver "path", cai para os pontos nomeados ("stops"). Os pontos nomeados
// aparecem como marcadores com o nome do local.
function drawRoutes(lines) {
  const bounds = [];

  lines.forEach((line) => {
    const stops = line.stops || [];
    const path = line.path && line.path.length >= 2 ? line.path : stops.map((s) => [s.lat, s.lng]);
    if (path.length < 2) return;

    // contorno escuro por baixo + linha principal por cima (efeito "estrada")
    L.polyline(path, {
      color: "#0b3d24",
      weight: 10,
      opacity: 0.9,
      lineJoin: "round",
      lineCap: "round",
    }).addTo(map);
    L.polyline(path, {
      color: line.color,
      weight: 6,
      opacity: 1,
      lineJoin: "round",
      lineCap: "round",
    })
      .bindTooltip(line.name, { sticky: true })
      .addTo(map);

    path.forEach((p) => bounds.push(p));

    stops.forEach((stop) => {
      L.circleMarker([stop.lat, stop.lng], {
        radius: 7,
        color: "#ffffff",
        weight: 2,
        fillColor: "#0b3d24",
        fillOpacity: 1,
      })
        .bindTooltip(stop.name, { permanent: true, direction: "right", offset: [8, 0] })
        .addTo(map);
    });
  });

  // enquadra o mapa na rota, para as ruas aparecerem em detalhe
  if (bounds.length) {
    map.fitBounds(bounds, { padding: [40, 40] });
  }
}

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
