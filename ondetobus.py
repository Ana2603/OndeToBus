"""
OndeToBus — protótipo de rastreamento colaborativo de ônibus (Projeto Integrador I).
A recarga de cartão é SIMULADA.
 
Rodar:  pip install flask  &&  python ondetobus.py   ->  http://localhost:5000
Variáveis opcionais: PORT, FLASK_DEBUG ("1" liga debug), DB_PATH.
"""
 
import os
import sqlite3
import time
import json
import urllib.request
import threading
from pathlib import Path
from flask import Flask, jsonify, request, g, render_template, send_from_directory
 
BASE_DIR = Path(__file__).parent
DB_PATH = Path(os.environ.get("DB_PATH", BASE_DIR / "onibus.db"))
 
app = Flask(__name__)
 
# Linha 02 · Noêmia / Santa Helena / UFSM — com as DUAS viagens (rotas).
# "stops" = pontos nomeados (como na primeira versão do mapa).
# "routes" = traçados de cada viagem; os pontos são aproximados e o servidor
# os encaixa nas ruas reais via OSRM (dados do OpenStreetMap).
LINES = [
    {
        "id": "02",
        "name": "02 · Noêmia / Santa Helena / UFSM",
        "color": "#1B7F4B",
        "stops": [
            {"name": "UFSM · Campus Cachoeira do Sul", "lat": -30.01397, "lng": -52.93531},
            {"name": "Stok Center", "lat": -30.01643, "lng": -52.91947},
            {"name": "Medianeira", "lat": -30.02198, "lng": -52.91623},
            {"name": "Santa Helena", "lat": -30.02871, "lng": -52.91037},
            {"name": "Centro", "lat": -30.04012, "lng": -52.89722},
            {"name": "Hospital de Caridade (H)", "lat": -30.04550, "lng": -52.89500},
        ],
        "routes": [
            {
                "name": "Viagem 1",
                "color": "#1B7F4B",
                "path": [
                    [-30.01921, -52.93665],
                    [-30.01321, -52.93733],
                    [-30.01397, -52.93531],
                    [-30.01660, -52.92351],
                    [-30.01643, -52.91947],
                    [-30.02198, -52.91623],
                    [-30.02725, -52.91071],
                    [-30.03134, -52.90956],
                    [-30.03339, -52.90599],
                    [-30.03866, -52.89857],
                    [-30.04012, -52.89722],
                    [-30.04363, -52.89352],
                    [-30.04550, -52.89500],
                ],
            },
            {
                "name": "Viagem 2",
                "color": "#E67E22",
                "path": [
                    [-30.01921, -52.93665],
                    [-30.01321, -52.93733],
                    [-30.01397, -52.93531],
                    [-30.01660, -52.92351],
                    [-30.01643, -52.91947],
                    [-30.0183, -52.9252],
                    [-30.0212, -52.9246],
                    [-30.0205, -52.9205],
                    [-30.0240, -52.9237],
                    [-30.0259, -52.9243],
                    [-30.0255, -52.9202],
                    [-30.0267, -52.9186],
                    [-30.0345, -52.9170],
                    [-30.0332, -52.9097],
                    [-30.0366, -52.9031],
                    [-30.04012, -52.89722],
                    [-30.04550, -52.89500],
                ],
            },
        ],
    },
]
 
_PATH_CACHE = {}  # chave -> (timestamp, caminho, deu_certo)
_RETRY_SECONDS = 300
 
 
def _osrm(kind, coords):
    pts = ";".join(f"{lng},{lat}" for lat, lng in coords)
    base = "https://router.project-osrm.org"
    if kind == "match":
        radii = ";".join(["60"] * len(coords))
        url = f"{base}/match/v1/driving/{pts}?geometries=geojson&overview=full&radiuses={radii}"
        key = "matchings"
    else:
        url = f"{base}/route/v1/driving/{pts}?geometries=geojson&overview=full"
        key = "routes"
    req = urllib.request.Request(url, headers={"User-Agent": "OndeToBus/1.0 (projeto academico UFSM)"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        data = json.load(resp)
    if data.get("code") != "Ok" or not data.get(key):
        return None
    flat = []
    for item in data[key]:
        for lng, lat in item["geometry"]["coordinates"]:
            flat.append([lat, lng])
    return flat or None
 
 
_BUSY = set()
 
 
def _worker(key, raw):
    result = None
    for kind in ("route", "match"):
        try:
            result = _osrm(kind, raw)
            if result:
                break
        except Exception:
            pass
    if result:
        _PATH_CACHE[key] = (time.time(), result, True)
    else:
        _PATH_CACHE[key] = (time.time(), raw, False)
    _BUSY.discard(key)
 
 
def snapped(key, raw):
    """Nunca bloqueia: devolve o traçado já encaixado (se existir) ou o bruto,
    e calcula o encaixe nas ruas em segundo plano. Retorna (caminho, encaixado?)."""
    if len(raw) < 2:
        return raw, False
    cached = _PATH_CACHE.get(key)
    if cached and cached[2]:
        return cached[1], True
    if key not in _BUSY and (not cached or time.time() - cached[0] > _RETRY_SECONDS):
        _BUSY.add(key)
        threading.Thread(target=_worker, args=(key, raw), daemon=True).start()
    return raw, False
 
 
def line_routes(line):
    out = []
    for i, r in enumerate(line.get("routes", [])):
        path, ok = snapped(f'{line["id"]}-{i}', r["path"])
        out.append({
            "name": r["name"],
            "color": r.get("color", line["color"]),
            "path": path,
            "snapped": ok,
        })
    return out
 
 
STATUS_LABELS = {"no_horario": "No horário", "atrasado": "Atrasado", "quebrado": "Quebrado"}
 
 
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
        """CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT, line_id TEXT NOT NULL,
            lat REAL NOT NULL, lng REAL NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL)"""
    )
    conn.execute("CREATE TABLE IF NOT EXISTS cards (number TEXT PRIMARY KEY, balance REAL NOT NULL)")
    conn.commit()
    if fresh:
        now = time.time()
        for line in LINES:
            first = line["stops"][0]
            conn.execute(
                "INSERT INTO reports (line_id, lat, lng, status, created_at) VALUES (?,?,?,?,?)",
                (line["id"], first["lat"], first["lng"], "no_horario", now - 60),
            )
        conn.execute("INSERT OR IGNORE INTO cards (number, balance) VALUES (?, ?)", ("**** 4821", 12.40))
        conn.commit()
    conn.close()
 
 
@app.route("/")
def index():
    return render_template("index.html", lines=LINES)
 
 
@app.route("/sw.js")
def service_worker():
    response = send_from_directory(app.static_folder, "sw.js")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Content-Type"] = "application/javascript"
    return response
 
 
@app.route("/api/lines")
def api_lines():
    return jsonify([{**l, "routes": line_routes(l)} for l in LINES])
 
 
@app.route("/api/status")
def api_status():
    db = get_db()
    result = []
    for line in LINES:
        row = db.execute(
            "SELECT * FROM reports WHERE line_id = ? ORDER BY created_at DESC LIMIT 1", (line["id"],)
        ).fetchone()
        reporters = db.execute(
            "SELECT COUNT(*) AS c FROM reports WHERE line_id = ? AND created_at > ?",
            (line["id"], time.time() - 900),
        ).fetchone()["c"]
        result.append({
            "id": line["id"],
            "name": line["name"],
            "color": line["color"],
            "stops": line.get("stops", []),
            "routes": line_routes(line),
            "status": row["status"] if row else None,
            "status_label": STATUS_LABELS.get(row["status"]) if row else "Sem relatos",
            "lat": row["lat"] if row else None,
            "lng": row["lng"] if row else None,
            "seconds_ago": int(time.time() - row["created_at"]) if row else None,
            "reporters": reporters,
        })
    return jsonify(result)
 
 
@app.route("/api/report", methods=["POST"])
def api_report():
    data = request.get_json(force=True) or {}
    line_id, lat, lng = data.get("line_id"), data.get("lat"), data.get("lng")
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
    return jsonify({"ok": True, "simulated": True, "card_number": card_number,
                    "amount": amount, "new_balance": new_balance})
 
 
@app.route("/api/card")
def api_card():
    card_number = request.args.get("number", "**** 4821")
    row = get_db().execute("SELECT * FROM cards WHERE number = ?", (card_number,)).fetchone()
    return jsonify({"card_number": card_number, "balance": row["balance"] if row else 0})
 
 
init_db()  # roda também sob gunicorn (Render), não só com "python ondetobus.py"
 
 
if __name__ == "__main__":
    porta = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=porta, debug=os.environ.get("FLASK_DEBUG", "0") == "1")
