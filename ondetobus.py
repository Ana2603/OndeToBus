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
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from flask import Flask, jsonify, request, g, render_template, send_from_directory

BASE_DIR = Path(__file__).parent
DB_PATH = Path(os.environ.get("DB_PATH", BASE_DIR / "onibus.db"))
# Cache do traçado já encaixado nas ruas (evita chamar o OSRM a cada reinício).
PATH_CACHE_FILE = DB_PATH.with_name("path_cache.json")
TZ = ZoneInfo("America/Sao_Paulo")

app = Flask(__name__)

# Linha 02 · Noêmia / Santa Helena / UFSM (Cachoeira do Sul).
# "stops" = pontos nomeados; "path" = traçado aproximado (ordem UFSM → Hospital),
# encaixado nas ruas reais pelo OSRM em snapped_path().
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
            [-30.00474, -52.94069],
            [-30.00522, -52.93939],
            [-30.00642, -52.93745],
            [-30.00691, -52.93183],
            [-30.00680, -52.92924],
            [-30.00642, -52.92803],
            [-30.00829, -52.92751],
            [-30.01035, -52.92717],
            [-30.00997, -52.92470],
            [-30.01128, -52.92406],
            [-30.01371, -52.92362],
            [-30.01633, -52.92289],
            [-30.01764, -52.92060],
            [-30.01858, -52.91887],
            [-30.02007, -52.91714],
            [-30.02194, -52.91498],
            [-30.02306, -52.91369],
            [-30.02419, -52.91261],
            [-30.02538, -52.91347],
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

# --- Traçado nas ruas (OSRM + cache em disco) ------------------------------
_PATH_CACHE = {}  # line_id -> (timestamp, caminho, deu_certo)
_RETRY_SECONDS = 300
_GEOM = {}  # line_id -> (id(path), distâncias acumuladas) p/ a simulação


def _osrm(coords):
    pts = ";".join(f"{lng},{lat}" for lat, lng in coords)
    url = ("https://router.project-osrm.org/route/v1/driving/"
           f"{pts}?geometries=geojson&overview=full&continue_straight=true")
    req = urllib.request.Request(
        url, headers={"User-Agent": "OndeToBus/1.0 (projeto academico UFSM)"})
    with urllib.request.urlopen(req, timeout=12) as resp:
        data = json.load(resp)
    if data.get("code") != "Ok" or not data.get("routes"):
        return None
    return [[lat, lng] for lng, lat in data["routes"][0]["geometry"]["coordinates"]] or None


def _load_disk_cache():
    try:
        return json.loads(PATH_CACHE_FILE.read_text())
    except Exception:
        return {}


def street_path(line):
    """Só o traçado JÁ encaixado nas ruas; [] se o OSRM não respondeu
    (melhor não desenhar do que desenhar uma linha fora das ruas)."""
    snapped_path(line)
    c = _PATH_CACHE.get(line["id"])
    return c[1] if c and c[2] else []


def snapped_path(line):
    raw = line.get("path") or []
    if len(raw) < 2:
        return raw
    lid = line["id"]
    cached = _PATH_CACHE.get(lid)
    if cached:
        ts, path, ok = cached
        if ok or time.time() - ts < _RETRY_SECONDS:
            return path
    disk = _load_disk_cache().get(lid)
    if disk and disk.get("raw") == raw:
        _PATH_CACHE[lid] = (time.time(), disk["path"], True)
        return disk["path"]
    result = None
    for _ in range(2):
        try:
            result = _osrm(raw)
        except Exception:
            result = None
        if result:
            break
    if result:
        _PATH_CACHE[lid] = (time.time(), result, True)
        try:
            allc = _load_disk_cache()
            allc[lid] = {"raw": raw, "path": result}
            PATH_CACHE_FILE.write_text(json.dumps(allc))
        except Exception:
            pass
        return result
    _PATH_CACHE[lid] = (time.time(), raw, False)
    return raw


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
    return jsonify([{**l, "path": street_path(l)} for l in LINES])


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
