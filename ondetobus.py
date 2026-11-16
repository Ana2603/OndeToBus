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
            {"name": "Santa Helena", "lat": -30.01633, "lng": -52.92289},
            {"name": "Centro", "lat": -30.02194, "lng": -52.91498},
            {"name": "Hospital (HCB)", "lat": -30.02538, "lng": -52.91347},
        ],
        "path": [
            [-30.00418, -52.940712],
            [-30.004717, -52.940048],
            [-30.005253, -52.937436],
            [-30.005905, -52.937259],
            [-30.006135, -52.936639],
            [-30.006442, -52.929645],
            [-30.006442, -52.928051],
            [-30.010045, -52.92721],
            [-30.009815, -52.924908],
            [-30.00947, -52.924421],
            [-30.013648, -52.923403],
            [-30.014108, -52.923137],
            [-30.014913, -52.923137],
            [-30.016447, -52.922694],
            [-30.016868, -52.922252],
            [-30.016983, -52.921322],
            [-30.018363, -52.918577],
            [-30.01913, -52.917559],
            [-30.02005, -52.916806],
            [-30.020088, -52.916231],
            [-30.020472, -52.915744],
            [-30.020472, -52.915478],
            [-30.020893, -52.915523],
            [-30.021545, -52.914549],
            [-30.022657, -52.914017],
            [-30.022963, -52.913663],
            [-30.022963, -52.913265],
            [-30.0235, -52.912822],
            [-30.02488, -52.911937],
            [-30.025072, -52.912114],
            [-30.0258, -52.912114],
            [-30.026413, -52.912734],
            [-30.026413, -52.913486],
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


def _osrm_match(raw):
    step = max(40.0, _length(raw) / 90)
    pts = _densify(raw, step)
    while len(pts) > 100:
        step *= 1.15
        pts = _densify(raw, step)
    coords = ";".join(f"{lng:.5f},{lat:.5f}" for lat, lng in pts)
    radii = ";".join("45" for _ in pts)
    url = ("https://router.project-osrm.org/match/v1/driving/" + coords +
           f"?geometries=geojson&overview=full&gaps=ignore&radiuses={radii}")
    req = urllib.request.Request(
        url, headers={"User-Agent": "OndeToBus/1.0 (projeto academico UFSM)"})
    with urllib.request.urlopen(req, timeout=8) as resp:
        data = json.load(resp)
    if data.get("code") != "Ok" or not data.get("matchings"):
        return None
    out = [[lat, lng] for m in data["matchings"]
           for lng, lat in m["geometry"]["coordinates"]]
    ratio = _length(out) / max(_length(raw), 1)
    return out if 0.85 <= ratio <= 1.3 else None


def street_info(line):
    """(caminho, encaixado_nas_ruas?)"""
    raw = line.get("path") or []
    lid = line["id"]
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
                result = _osrm_match(raw)
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
        {**l, "path": street_path(l), "snapped": street_info(l)[1],
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
