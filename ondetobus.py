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
# Única rota: Hospital (HCB) → UFSM. "path" já segue as ruas (traçado do app
# oficial), então não depende de nenhum serviço externo.
LINES = [
    {
        "id": "02",
        "name": "02 · Noêmia / Santa Helena / UFSM",
        "color": "#1B7F4B",
        "stops": [
            {"name": "Hospital (HCB)", "lat": -30.02538, "lng": -52.91347},
            {"name": "Centro", "lat": -30.02205, "lng": -52.91609},
            {"name": "Santa Helena", "lat": -30.01753, "lng": -52.92330},
            {"name": "Medianeira", "lat": -30.01076, "lng": -52.92792},
            {"name": "Stok Center", "lat": -30.00680, "lng": -52.92924},
            {"name": "UFSM · Campus Cachoeira do Sul", "lat": -30.00642, "lng": -52.94004},
        ],
        "path": [
            [-30.02538, -52.91347],
            [-30.02482, -52.91356],
            [-30.02326, -52.91488],
            [-30.02205, -52.91609],
            [-30.02135, -52.91709],
            [-30.02048, -52.91829],
            [-30.01927, -52.91950],
            [-30.01823, -52.92150],
            [-30.01753, -52.92330],
            [-30.01649, -52.92371],
            [-30.01458, -52.92431],
            [-30.01197, -52.92479],
            [-30.01111, -52.92511],
            [-30.01128, -52.92591],
            [-30.01145, -52.92732],
            [-30.01180, -52.92772],
            [-30.01076, -52.92792],
            [-30.00954, -52.92824],
            [-30.00781, -52.92840],
            [-30.00822, -52.92952],
            [-30.00833, -52.93193],
            [-30.00798, -52.93714],
            [-30.00670, -52.93894],
            [-30.00642, -52.94004],
            [-30.00763, -52.93995],
            [-30.00954, -52.94027],
        ],
    },
]

# Tabela de horários da Linha 002 (Cachoeira Mobilidade) — DIAS ÚTEIS.
# Cada viagem: (saída, chegada) — simuladas no sentido HCB → UFSM.
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

# --- Geometria da rota (calculada uma única vez) -----------------------------
def _cumulative(path):
    cum = [0.0]
    for (la1, lo1), (la2, lo2) in zip(path, path[1:]):
        dx = (lo2 - lo1) * math.cos(math.radians((la1 + la2) / 2))
        cum.append(cum[-1] + math.hypot(la2 - la1, dx))
    return cum


_CUM = {l["id"]: _cumulative(l["path"]) for l in LINES}


def point_at(line, frac):
    """Ponto a `frac` (0..1) da distância total do traçado."""
    path, cum = line["path"], _CUM[line["id"]]
    target = cum[-1] * max(0.0, min(1.0, frac))
    lo, hi = 0, len(cum) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if cum[mid] <= target:
            lo = mid
        else:
            hi = mid
    t = (target - cum[lo]) / (cum[hi] - cum[lo] or 1)
    (la1, lo1), (la2, lo2) = path[lo], path[hi]
    return la1 + (la2 - la1) * t, lo1 + (lo2 - lo1) * t


# --- Simulação do ônibus pela tabela de horários ----------------------------
def simulated_bus(line, now=None):
    """Viagem em andamento (HCB → UFSM) ou None. Só em dias úteis."""
    now = now or datetime.now(TZ)
    if now.weekday() > 4:
        return None
    m = now.hour * 60 + now.minute + now.second / 60
    for dep, arr, tag in TIMETABLE.get(line["id"], []):
        if dep <= m <= arr:
            lat, lng = point_at(line, (m - dep) / (arr - dep))
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
    return jsonify([{**l, "path": l["path"]} for l in LINES])


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
            "path": line["path"],
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
