"""
OndeToBus — protótipo de rastreamento colaborativo de ônibus.

Projeto acadêmico (Projeto Integrador I). A localização dos ônibus vem dos
próprios usuários (rastreamento colaborativo) e, para a Linha 02, também de
uma SIMULAÇÃO baseada na tabela de horários oficial (dias úteis). A "recarga
de cartão" é SIMULADA — não processa pagamentos reais.

Como rodar:
    pip install -r requirements.txt
    python ondetobus.py
Depois abra http://localhost:5000 no navegador.

Variáveis de ambiente opcionais:
    PORT        — porta do servidor (padrão 5000).
    FLASK_DEBUG — "1" liga o modo debug (deixe desligado em produção).
    DB_PATH     — caminho do arquivo SQLite (útil com Persistent Disk).
"""

import os
import sqlite3
import time
import json
import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from flask import Flask, jsonify, request, g, render_template, send_from_directory

BASE_DIR = Path(__file__).parent
DB_PATH = Path(os.environ.get("DB_PATH", BASE_DIR / "onibus.db"))
TZ = ZoneInfo("America/Sao_Paulo")

app = Flask(__name__)

# Linha 02 · Noêmia / Santa Helena / UFSM (Cachoeira do Sul).
# "stops" = pontos nomeados; "path" = traçado aproximado (ordem UFSM → Hospital),
# encaixado nas ruas reais pelo OSRM, no navegador (static/app.js).
LINES = [
    {
        "id": "02",
        "name": "02 · Noêmia / Santa Helena / UFSM",
        "color": "#1B7F4B",
        "stops": [
            {"name": "UFSM · Campus Cachoeira do Sul", "lat": -30.00641943463219, "lng": -52.94003979869694},
            {"name": "Stok Center", "lat": -30.00680, "lng": -52.92924},
            {"name": "Medianeira", "lat": -30.01035, "lng": -52.92717},
            {"name": "Santa Helena", "lat": -30.014634, "lng": -52.923358},
            {"name": "Centro", "lat": -30.02194, "lng": -52.91498},
            {"name": "Hospital (HCB)", "lat": -30.02538, "lng": -52.91347},
        ],
        # Traçados já ENCAIXADOS NAS RUAS (extraídos do mapa de referência, onde as
        # linhas seguiam as ruas do OpenStreetMap). Ordem: UFSM -> HCB.
        "street_ready": True,
        "path": [
            [-30.00752, -52.94138],
            [-30.006949, -52.941336],
            [-30.006683, -52.941424],
            [-30.005846, -52.942259],
            [-30.003866, -52.942215],
            [-30.003029, -52.942127],
            [-30.002953, -52.942039],
            [-30.003067, -52.941248],
            [-30.003447, -52.940633],
            [-30.003447, -52.940457],
            [-30.003676, -52.940193],
            [-30.003676, -52.939973],
            [-30.003866, -52.939754],
            [-30.003866, -52.93949],
            [-30.004094, -52.939226],
            [-30.004094, -52.939006],
            [-30.004285, -52.938787],
            [-30.004285, -52.938479],
            [-30.004589, -52.938083],
            [-30.004551, -52.937907],
            [-30.004742, -52.937424],
            [-30.00478, -52.936589],
            [-30.004932, -52.935973],
            [-30.005008, -52.934655],
            [-30.005427, -52.932369],
            [-30.007635, -52.931929],
            [-30.00752, -52.931666],
            [-30.007216, -52.929776],
            [-30.006531, -52.928896],
            [-30.006493, -52.928765],
            [-30.006607, -52.928501],
            [-30.006759, -52.928325],
            [-30.007102, -52.928237],
            [-30.008015, -52.928193],
            [-30.008206, -52.928061],
            [-30.008206, -52.927841],
            [-30.008206, -52.928061],
            [-30.008282, -52.928105],
            [-30.009347, -52.928017],
            [-30.009576, -52.928149],
            [-30.010147, -52.928061],
            [-30.010375, -52.927797],
            [-30.010375, -52.927226],
            [-30.010071, -52.926874],
            [-30.009842, -52.925028],
            [-30.00969, -52.924677],
            [-30.00969, -52.924501],
            [-30.009956, -52.924325],
            [-30.011251, -52.924105],
            [-30.011479, -52.923973],
            [-30.012963, -52.92371],
            [-30.013154, -52.923578],
            [-30.013991, -52.923402],
            [-30.014752, -52.923358],
            [-30.014867, -52.923226],
            [-30.017112, -52.922742],
            [-30.017188, -52.922215],
            [-30.017112, -52.922083],
            [-30.017074, -52.92138],
            [-30.01696, -52.921248],
            [-30.016884, -52.920281],
            [-30.016922, -52.921072],
            [-30.017112, -52.921424],
            [-30.017379, -52.921512],
            [-30.017493, -52.92138],
            [-30.018064, -52.921292],
            [-30.018292, -52.920984],
            [-30.018292, -52.920764],
            [-30.018939, -52.919797],
            [-30.018977, -52.919578],
            [-30.019205, -52.919314],
            [-30.019205, -52.919094],
            [-30.019662, -52.918479],
            [-30.019662, -52.918259],
            [-30.020119, -52.917644],
            [-30.020119, -52.917424],
            [-30.020576, -52.916808],
            [-30.020576, -52.916589],
            [-30.020766, -52.916369],
            [-30.020766, -52.916105],
            [-30.021032, -52.916149],
            [-30.02187, -52.915622],
            [-30.021984, -52.914918],
            [-30.021413, -52.914874],
            [-30.021261, -52.914698],
            [-30.02107, -52.914698],
            [-30.020461, -52.914127],
            [-30.02088, -52.913819],
            [-30.021185, -52.913819],
            [-30.021413, -52.914039],
            [-30.021565, -52.913863],
            [-30.021755, -52.913863],
            [-30.021946, -52.913644],
            [-30.022136, -52.913644],
            [-30.022326, -52.913424],
            [-30.022517, -52.913424],
            [-30.022707, -52.913204],
            [-30.022897, -52.913204],
            [-30.023088, -52.912984],
            [-30.02324, -52.912984],
            [-30.023468, -52.91272],
            [-30.023658, -52.91272],
            [-30.023849, -52.912501],
            [-30.024343, -52.912413],
            [-30.024724, -52.912061],
            [-30.02499, -52.912369],
            [-30.02499, -52.912545],
            [-30.025105, -52.912676],
            [-30.025105, -52.913072],
            [-30.025371, -52.913512],
        ],
        "path_volta": [
            [-30.00752, -52.94138],
            [-30.006949, -52.941336],
            [-30.006683, -52.941424],
            [-30.005846, -52.942259],
            [-30.003866, -52.942215],
            [-30.003029, -52.942127],
            [-30.002953, -52.942039],
            [-30.003067, -52.941248],
            [-30.003447, -52.940633],
            [-30.003447, -52.940457],
            [-30.003676, -52.940193],
            [-30.003676, -52.939973],
            [-30.003866, -52.939754],
            [-30.003866, -52.93949],
            [-30.004094, -52.939226],
            [-30.004094, -52.939006],
            [-30.004285, -52.938787],
            [-30.004285, -52.938479],
            [-30.004589, -52.938083],
            [-30.004551, -52.937907],
            [-30.004742, -52.937424],
            [-30.00478, -52.936589],
            [-30.004932, -52.935973],
            [-30.005008, -52.934655],
            [-30.005427, -52.932369],
            [-30.007635, -52.931929],
            [-30.007939, -52.932105],
            [-30.008206, -52.932721],
            [-30.008548, -52.932369],
            [-30.009347, -52.932237],
            [-30.009538, -52.932105],
            [-30.009728, -52.932281],
            [-30.009728, -52.932589],
            [-30.009728, -52.931973],
            [-30.009614, -52.931841],
            [-30.009347, -52.929819],
            [-30.009766, -52.929512],
            [-30.010527, -52.92938],
            [-30.010642, -52.929512],
            [-30.010565, -52.929424],
            [-30.010489, -52.929512],
            [-30.010642, -52.930479],
            [-30.011098, -52.930567],
            [-30.011631, -52.930435],
            [-30.011898, -52.931094],
            [-30.011936, -52.93171],
            [-30.011707, -52.931622],
            [-30.011327, -52.931666],
            [-30.010984, -52.931973],
            [-30.010946, -52.932281],
            [-30.012012, -52.932369],
            [-30.01205, -52.932589],
            [-30.012583, -52.933248],
            [-30.012811, -52.933732],
            [-30.013687, -52.934655],
            [-30.013725, -52.934567],
            [-30.013915, -52.934743],
            [-30.015209, -52.934743],
            [-30.015437, -52.934215],
            [-30.015437, -52.933907],
            [-30.015552, -52.933776],
            [-30.015628, -52.932984],
            [-30.015514, -52.932809],
            [-30.014714, -52.927358],
            [-30.015247, -52.927006],
            [-30.016313, -52.926874],
            [-30.016503, -52.926655],
            [-30.016846, -52.926655],
            [-30.016922, -52.926567],
            [-30.017264, -52.926567],
            [-30.017341, -52.926655],
            [-30.017759, -52.926391],
            [-30.017988, -52.926655],
            [-30.018026, -52.927534],
            [-30.018444, -52.927622],
            [-30.018939, -52.92749],
            [-30.019053, -52.927622],
            [-30.019053, -52.925995],
            [-30.018901, -52.925819],
            [-30.018749, -52.924545],
            [-30.018635, -52.924413],
            [-30.018635, -52.924017],
            [-30.018482, -52.923797],
            [-30.01833, -52.923753],
            [-30.017531, -52.923885],
            [-30.017341, -52.923666],
            [-30.017417, -52.923534],
            [-30.017379, -52.923094],
            [-30.017264, -52.922874],
            [-30.017036, -52.92138],
            [-30.017797, -52.92138],
            [-30.018216, -52.92094],
            [-30.018444, -52.920457],
            [-30.018711, -52.920149],
            [-30.018711, -52.919973],
            [-30.018901, -52.919753],
            [-30.018901, -52.919622],
            [-30.019167, -52.919314],
            [-30.019167, -52.919094],
            [-30.019624, -52.918479],
            [-30.019624, -52.918259],
            [-30.020081, -52.917644],
            [-30.020081, -52.917424],
            [-30.020538, -52.916808],
            [-30.020538, -52.916589],
            [-30.020728, -52.916369],
            [-30.020766, -52.916105],
            [-30.021223, -52.916105],
            [-30.021451, -52.915841],
            [-30.021679, -52.915841],
            [-30.021832, -52.915665],
            [-30.021984, -52.915665],
            [-30.022212, -52.915402],
            [-30.022402, -52.915402],
            [-30.023011, -52.914698],
            [-30.022745, -52.914391],
            [-30.022402, -52.914435],
            [-30.021984, -52.914918],
            [-30.022441, -52.914479],
            [-30.022707, -52.914391],
            [-30.023278, -52.914786],
            [-30.023392, -52.914655],
            [-30.023582, -52.914655],
            [-30.023773, -52.914435],
            [-30.023963, -52.914435],
            [-30.024153, -52.914215],
            [-30.024343, -52.914215],
            [-30.024534, -52.913995],
            [-30.024724, -52.913995],
            [-30.025371, -52.913512],
        ],
    },
]

# Tabela de horários da Linha 002 (Cachoeira Mobilidade) — DIAS ÚTEIS.
# Cada viagem: (saída da UFSM, chegada ao HCB). A tabela só traz o sentido
# UFSM → HCB; o sentido HCB → UFSM não consta nela.
_A = ["6:00-6:30", "7:00-7:30", "7:15-7:45", "8:00-8:30", "9:10-9:45", "10:20-11:00",
      "11:30-12:10", "12:40-13:20", "13:50-14:30", "15:00-15:40", "16:10-16:50",
      "17:20-18:00", "18:30-19:10", "19:30-20:10", "20:50-21:30", "22:05-22:45",
      "23:00-23:40"]
_B = ["6:30-7:00", "7:30-8:05", "8:35-9:15", "9:45-10:25", "10:55-11:35", "12:05-12:45",
      "13:15-13:55", "14:25-15:05", "15:35-16:15", "16:45-17:25", "17:55-18:35",
      "19:05-19:45"]


def _mins(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


TIMETABLE = {
    "02": [
        (_mins(a), _mins(b), tag)
        for tag, rows in (("002A · H.U.", _A), ("002B · Rodoviária", _B))
        for a, b in (r.split("-") for r in rows)
    ]
}
TIMETABLE["02"].sort()

# --- Traçado nas ruas ---------------------------------------------------------
# "path" acima foi extraído da imagem do app. Aqui ele é encaixado nas ruas
# reais com o OSRM (dados do OpenStreetMap), UMA vez: o resultado fica em
# memória e em disco (path_cache.json), então não gasta rede a cada pedido.
# Se o OSRM não responder, devolve o traçado original e tenta de novo em 5 min
# (o navegador também tenta encaixar por conta própria — veja static/app.js).
import urllib.request

PATH_CACHE_FILE = DB_PATH.with_name("path_cache.json")
_SNAP = {}  # line_id -> {"path": [...], "ok": bool, "ts": float}
_RETRY_SECONDS = 300
_GEOM = {}


def _meters(a, b):
    return math.hypot((b[0] - a[0]) * 111320,
                      (b[1] - a[1]) * 111320 * math.cos(math.radians(a[0])))


def _densify(p, step):
    out = [p[0]]
    for a, b in zip(p, p[1:]):
        n = max(1, round(_meters(a, b) / step))
        for k in range(1, n + 1):
            out.append([a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n])
    return out


def _length(p):
    return sum(_meters(a, b) for a, b in zip(p, p[1:]))


_LAST_SNAP_ERROR = {}


def _osrm_call(kind, pts, radius=None):
    coords = ";".join(f"{lng:.5f},{lat:.5f}" for lat, lng in pts)
    if kind == "match":
        radii = ";".join(str(radius) for _ in pts)
        url = ("https://router.project-osrm.org/match/v1/driving/" + coords +
               f"?geometries=geojson&overview=full&gaps=ignore&radiuses={radii}")
        key = "matchings"
    else:
        url = ("https://router.project-osrm.org/route/v1/driving/" + coords +
               "?geometries=geojson&overview=full&continue_straight=true")
        key = "routes"
    req = urllib.request.Request(
        url, headers={"User-Agent": "OndeToBus/1.0 (projeto academico UFSM)"})
    with urllib.request.urlopen(req, timeout=8) as resp:
        data = json.load(resp)
    if data.get("code") != "Ok" or not data.get(key):
        raise RuntimeError(f"OSRM {kind}: {data.get('code')}")
    return [[lat, lng] for item in data[key]
            for lng, lat in item["geometry"]["coordinates"]]


def _limit(pts, n=100):
    step = 40.0
    while len(pts) > n:
        step *= 1.15
        pts = _densify(pts, step)
    return pts


def _osrm_match(raw, lid="?"):
    """Tenta, em ordem: match (raio 45 m), match (raio 100 m) e route com
    pontos intermediários. Aceita só resultados com tamanho parecido com o
    traçado original (evita desvios estranhos)."""
    dense = _limit(_densify(raw, max(40.0, _length(raw) / 90)))
    way = _limit(_densify(raw, max(60.0, _length(raw) / 45)), 60)
    attempts = (("match45", lambda: _osrm_call("match", dense, 45)),
                ("match100", lambda: _osrm_call("match", dense, 100)),
                ("route", lambda: _osrm_call("route", way)))
    errs = []
    for name, fn in attempts:
        try:
            out = fn()
            ratio = _length(out) / max(_length(raw), 1)
            if 0.85 <= ratio <= 1.35:
                _LAST_SNAP_ERROR.pop(lid, None)
                return out
            errs.append(f"{name}: tamanho estranho ({ratio:.2f}x)")
        except Exception as e:
            errs.append(f"{name}: {e}")
    _LAST_SNAP_ERROR[lid] = " | ".join(errs)
    return None


def street_info(line):
    """(caminho, encaixado_nas_ruas?)"""
    raw = line.get("path") or []
    lid = line["id"]
    if line.get("street_ready"):
        return raw, True
    c = _SNAP.get(lid)
    if c and (c["ok"] or time.time() - c["ts"] < _RETRY_SECONDS):
        return c["path"], c["ok"]
    try:
        disk = json.loads(PATH_CACHE_FILE.read_text()).get(lid)
        if disk and disk.get("raw") == raw:
            _SNAP[lid] = {"path": disk["path"], "ok": True, "ts": time.time()}
            return disk["path"], True
    except Exception:
        pass
    result = None
    if len(raw) >= 2:
        for _ in range(1):
            try:
                result = _osrm_match(raw, lid)
            except Exception:
                result = None
            if result:
                break
    if result:
        _SNAP[lid] = {"path": result, "ok": True, "ts": time.time()}
        try:
            try:
                allc = json.loads(PATH_CACHE_FILE.read_text())
            except Exception:
                allc = {}
            allc[lid] = {"raw": raw, "path": result}
            PATH_CACHE_FILE.write_text(json.dumps(allc))
        except Exception:
            pass
        return result, True
    _SNAP[lid] = {"path": raw, "ok": False, "ts": time.time()}
    return raw, False


def street_path(line):
    return street_info(line)[0]


# --- Simulação do ônibus pela tabela de horários ----------------------------
def _cum_dist(path):
    key = id(path)
    hit = _GEOM.get(key)
    if hit:
        return hit
    cum = [0.0]
    for (la1, lo1), (la2, lo2) in zip(path, path[1:]):
        dx = (lo2 - lo1) * math.cos(math.radians((la1 + la2) / 2))
        cum.append(cum[-1] + math.hypot(la2 - la1, dx))
    _GEOM.clear()
    _GEOM[key] = cum
    return cum


def point_at(path, frac):
    """Ponto a `frac` (0..1) da distância total do traçado."""
    cum = _cum_dist(path)
    target = cum[-1] * max(0.0, min(1.0, frac))
    lo, hi = 0, len(cum) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if cum[mid] <= target:
            lo = mid
        else:
            hi = mid
    seg = cum[hi] - cum[lo] or 1
    t = (target - cum[lo]) / seg
    (la1, lo1), (la2, lo2) = path[lo], path[hi]
    return la1 + (la2 - la1) * t, lo1 + (lo2 - lo1) * t


def simulated_bus(line, now=None):
    """Viagem em andamento (UFSM → HCB) ou None. Só em dias úteis."""
    now = now or datetime.now(TZ)
    if now.weekday() > 4:
        return None
    m = now.hour * 60 + now.minute + now.second / 60
    path = street_path(line)
    if not path:
        return None
    for dep, arr, tag in TIMETABLE.get(line["id"], []):
        if dep <= m <= arr:
            lat, lng = point_at(path, (m - dep) / (arr - dep))
            return {"lat": lat, "lng": lng, "trip": tag,
                    "dep": f"{dep // 60}:{dep % 60:02d}", "arr": f"{arr // 60}:{arr % 60:02d}"}
    return None


def next_trip(line, now=None):
    now = now or datetime.now(TZ)
    if now.weekday() > 4:
        return None
    m = now.hour * 60 + now.minute
    for dep, arr, tag in TIMETABLE.get(line["id"], []):
        if dep > m:
            return {"trip": tag, "dep": f"{dep // 60}:{dep % 60:02d}"}
    return None


STATUS_LABELS = {
    "no_horario": "No horário",
    "atrasado": "Atrasado",
    "quebrado": "Quebrado",
}


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    fresh = not DB_PATH.exists()
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            line_id TEXT NOT NULL,
            lat REAL NOT NULL,
            lng REAL NOT NULL,
            status TEXT NOT NULL,
            created_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cards (
            number TEXT PRIMARY KEY,
            balance REAL NOT NULL
        )
        """
    )
    conn.commit()

    if fresh:
        now = time.time()
        for line in LINES:
            first = line["stops"][0]
            conn.execute(
                "INSERT INTO reports (line_id, lat, lng, status, created_at) VALUES (?,?,?,?,?)",
                (line["id"], first["lat"], first["lng"], "no_horario", now - 60),
            )
        conn.execute(
            "INSERT OR IGNORE INTO cards (number, balance) VALUES (?, ?)",
            ("**** 4821", 12.40),
        )
        conn.commit()
    conn.close()


@app.route("/")
def index():
    return render_template("index.html", lines=LINES)


@app.route("/sw.js")
def service_worker():
    # Na raiz para o service worker controlar o site inteiro.
    response = send_from_directory(app.static_folder, "sw.js")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Content-Type"] = "application/javascript"
    return response


@app.route("/api/lines")
def api_lines():
    return jsonify([
        {**l, "path": street_path(l), "path_volta": l.get("path_volta"), "snapped": street_info(l)[1], "snap_error": _LAST_SNAP_ERROR.get(l["id"]),
         "timetable": [[d, a, t] for d, a, t in TIMETABLE.get(l["id"], [])]}
        for l in LINES
    ])


@app.route("/api/status")
def api_status():
    """Posição/status mais recente de cada linha (simulação tem prioridade
    enquanto houver uma viagem da tabela em andamento)."""
    db = get_db()
    result = []
    for line in LINES:
        row = db.execute(
            "SELECT * FROM reports WHERE line_id = ? ORDER BY created_at DESC LIMIT 1",
            (line["id"],),
        ).fetchone()
        reporters = db.execute(
            "SELECT COUNT(*) AS c FROM reports WHERE line_id = ? AND created_at > ?",
            (line["id"], time.time() - 900),
        ).fetchone()["c"]

        sim = simulated_bus(line)
        entry = {
            "id": line["id"],
            "name": line["name"],
            "color": line["color"],
            "stops": line.get("stops", []),
            "path": street_path(line),
            "snapped": street_info(line)[1],
            "path_volta": line.get("path_volta"),
            "status": row["status"] if row else None,
            "status_label": STATUS_LABELS.get(row["status"]) if row else "Sem relatos",
            "lat": row["lat"] if row else None,
            "lng": row["lng"] if row else None,
            "seconds_ago": int(time.time() - row["created_at"]) if row else None,
            "reporters": reporters,
            "simulated": False,
            "next_trip": next_trip(line),
        }
        if sim:
            entry.update(
                status="no_horario",
                status_label=f"No horário · viagem {sim['dep']}→{sim['arr']} ({sim['trip']})",
                lat=sim["lat"], lng=sim["lng"], seconds_ago=0, simulated=True,
            )
        result.append(entry)
    return jsonify(result)


@app.route("/api/report", methods=["POST"])
def api_report():
    data = request.get_json(force=True) or {}
    line_id = data.get("line_id")
    lat = data.get("lat")
    lng = data.get("lng")
    status = data.get("status", "no_horario")

    if line_id not in {l["id"] for l in LINES}:
        return jsonify({"error": "Linha inválida"}), 400
    if status not in STATUS_LABELS:
        return jsonify({"error": "Status inválido"}), 400
    if lat is None or lng is None:
        return jsonify({"error": "Localização ausente"}), 400

    db = get_db()
    db.execute(
        "INSERT INTO reports (line_id, lat, lng, status, created_at) VALUES (?,?,?,?,?)",
        (line_id, lat, lng, status, time.time()),
    )
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/recharge", methods=["POST"])
def api_recharge():
    """Recarga SIMULADA — nenhum pagamento real é processado."""
    data = request.get_json(force=True) or {}
    card_number = data.get("card_number", "**** 4821")
    try:
        amount = float(data.get("amount", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "Valor inválido"}), 400

    if amount <= 0 or amount > 200:
        return jsonify({"error": "Informe um valor entre R$ 1 e R$ 200"}), 400

    db = get_db()
    row = db.execute("SELECT * FROM cards WHERE number = ?", (card_number,)).fetchone()
    if row is None:
        db.execute("INSERT INTO cards (number, balance) VALUES (?, ?)", (card_number, 0))
        balance = 0
    else:
        balance = row["balance"]

    new_balance = round(balance + amount, 2)
    db.execute("UPDATE cards SET balance = ? WHERE number = ?", (new_balance, card_number))
    db.commit()

    return jsonify(
        {
            "ok": True,
            "simulated": True,
            "card_number": card_number,
            "amount": amount,
            "new_balance": new_balance,
        }
    )


@app.route("/api/card")
def api_card():
    card_number = request.args.get("number", "**** 4821")
    db = get_db()
    row = db.execute("SELECT * FROM cards WHERE number = ?", (card_number,)).fetchone()
    balance = row["balance"] if row else 0
    return jsonify({"card_number": card_number, "balance": balance})


if __name__ == "__main__":
    init_db()
    porta = int(os.environ.get("PORT", 5000))
    modo_debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=porta, debug=modo_debug)
