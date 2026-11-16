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
import urllib.parse
from pathlib import Path
from flask import Flask, jsonify, request, g, render_template, send_from_directory

BASE_DIR = Path(__file__).parent
DB_PATH = Path(os.environ.get("DB_PATH", BASE_DIR / "onibus.db"))

app = Flask(__name__)

# Linha 02 · Noêmia / Santa Helena / UFSM — com as DUAS viagens (rotas).
# "stops" = pontos nomeados (como na primeira versão do mapa).
# "routes" = traçados de cada viagem; os pontos são aproximados e o servidor
# os encaixa nas ruas reais via OSRM (dados do OpenStreetMap).
def _enderecos(txt):
    out = []
    for ln in txt.strip().splitlines():
        ln = ln.strip()
        if ln:
            out.append(ln)
    return out


# Paradas (endereços) de cada sentido, na ordem do percurso, conforme o app da TNSG.
# O servidor geocodifica cada endereço (Nominatim/OSM) e traça a rota pelas ruas (OSRM).
UFSM_HCB = _enderecos("""
UFSM - Casa do Estudante
Rodovia Taufik Germano, 2825
Estrada da Ferreira, 1805
Rua Olímpia de Paula, 858
Rua Rodolfo Ricardo Germano Drews, 567
Rua Gustavo Peixoto, 2373
Rua Inácio Xavier, 1229
Rua Olímpia de Paula, 245
Rua São João, 1436
Rua Alarico Ribeiro, 2519
Rua Marcelo Gama, 4403
Rua Marcelo Gama, 55
Rua Marechal Hermes, 173
Rua Bento Gonçalves, 325
Avenida Júlio de Castilhos, 963
Rua Senador Pinheiro Machado, 79
Avenida Júlio de Castilhos, 459
Travessa Francisco Gama, 22
Rua Saldanha Marinho, 1139
Rua Saldanha Marinho, 946
Rua Saldanha Marinho, 757
Rua Saldanha Marinho, 536
Rua Saldanha Marinho, 283
Rua Tiradentes, 872
""")

HCB_UFSM = _enderecos("""
Rua Tiradentes, 872
Rua Moron, 656
Rua Conde de Porto Alegre, 1271
Rua Doutor Liberato S Viêira da Cunha, 530
Rua Lib. Salzano V. da Cunha, 660
Rua Quinze de Novembro, 682
Rua Quinze de Novembro, 928
Rua Comendador Fontoura, 1016
Rua Marechal Floriano, 124
Rua David Barcelos, 190
Rua David Barcelos, 408
Rua David Barcelos, 704
Avenida Brasil, 446
Avenida Brasil, 745
Avenida Brasil, 988
Avenida Brasil, 1368
Avenida Brasil, 1450
Avenida Brasil, 1671
Rua Alarico Ribeiro, 1378
Rua Alarico Ribeiro, 2080
Rua Mário Godoy Ilha, 188
Rua Mário Godoy Ilha, 404
Rua Marcelo Gama, 5430
Rodovia Taufik Germano, 1374
Rodovia Taufik Germano, 2083
Rodovia Taufik Germano, 2863
UFSM
UFSM - Casa do Estudante
""")

LINES = [
    {
        "id": "02",
        "name": "02 · Noêmia / Santa Helena / UFSM",
        "color": "#1B7F4B",
        "stops": [
            {"name": "UFSM · Campus Cachoeira do Sul", "lat": -30.01397, "lng": -52.93531},
            {"name": "Hospital de Caridade (H)", "lat": -30.04550, "lng": -52.89500},
        ],
        "routes": [
            {"name": "UFSM → HCB", "color": "#1B7F4B", "addresses": UFSM_HCB},
            {"name": "HCB → UFSM", "color": "#E67E22", "addresses": HCB_UFSM},
        ],
    },
]

# --- Geocodificação + traçado pelas ruas ---------------------------------
# Tudo roda em segundo plano (thread) e é guardado em route_cache.json, então
# o site abre na hora e as rotas aparecem quando ficarem prontas (~1-2 min na
# primeira vez; depois vem do cache).
CACHE_FILE = DB_PATH.parent / "route_cache.json"
UA = {"User-Agent": "OndeToBus/1.0 (projeto academico UFSM Cachoeira do Sul)"}
VIEWBOX = "-52.97,-29.99,-52.86,-30.07"  # Cachoeira do Sul
_GEO = {}      # endereço -> [lat, lng] ou None
_ROUTES = {}   # chave -> {"path": [...], "stops": [...], "ready": True}
_started = False


def _load_cache():
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        _GEO.update(data.get("geo", {}))
        _ROUTES.update(data.get("routes", {}))
    except Exception:
        pass


def _save_cache():
    try:
        CACHE_FILE.write_text(json.dumps({"geo": _GEO, "routes": _ROUTES}), encoding="utf-8")
    except Exception:
        pass


def _get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.load(resp)


def _nominatim(params):
    q = urllib.parse.urlencode({**params, "format": "json", "limit": 1,
                                "viewbox": VIEWBOX, "bounded": 1})
    data = _get("https://nominatim.openstreetmap.org/search?" + q)
    time.sleep(1.1)  # política de uso: 1 requisição por segundo
    if data:
        return [float(data[0]["lat"]), float(data[0]["lon"])]
    return None


def geocode(addr):
    if addr in _GEO and _GEO[addr]:
        return _GEO[addr]
    if addr.startswith("UFSM"):
        return [-30.01397, -52.93531] if "Casa" not in addr else [-30.01921, -52.93665]
    rua, _, num = addr.partition(",")
    rua = rua.replace("Lib.", "Libero").strip()
    num = num.strip()
    base = {"city": "Cachoeira do Sul", "state": "Rio Grande do Sul", "country": "Brasil"}
    res = None
    for params in (
        {**base, "street": f"{num} {rua}"} if num else None,
        {**base, "street": rua},
        {"q": f"{rua}, Cachoeira do Sul, RS"},
    ):
        if not params:
            continue
        try:
            res = _nominatim(params)
        except Exception:
            res = None
        if res:
            break
    _GEO[addr] = res
    return res


def _dist_km(a, b):
    import math
    dy = (a[0] - b[0]) * 111.0
    dx = (a[1] - b[1]) * 111.0 * math.cos(math.radians(a[0]))
    return math.hypot(dx, dy)


def _build_route(key, route):
    stops = []
    for addr in route["addresses"]:
        pt = geocode(addr)
        if not pt:
            continue
        if stops and _dist_km(stops[-1]["pt"], pt) > 3:   # descarta ponto fora do lugar
            continue
        if stops and stops[-1]["pt"] == pt:                # mesmo ponto repetido
            continue
        stops.append({"label": addr, "pt": pt})
    pts = [s["pt"] for s in stops]
    path = pts
    if len(pts) >= 2:
        for _ in range(2):
            try:
                coords = ";".join(f"{lng},{lat}" for lat, lng in pts)
                url = ("https://router.project-osrm.org/route/v1/driving/" + coords +
                       "?geometries=geojson&overview=full&continue_straight=true")
                data = _get(url)
                if data.get("code") == "Ok" and data.get("routes"):
                    path = [[la, ln] for ln, la in data["routes"][0]["geometry"]["coordinates"]]
                    break
            except Exception:
                time.sleep(2)
    _ROUTES[key] = {
        "path": path,
        "stops": [{"name": s["label"], "lat": s["pt"][0], "lng": s["pt"][1]} for s in stops],
        "ready": len(pts) >= 2,
    }
    _save_cache()


def _worker():
    for line in LINES:
        for i, r in enumerate(line["routes"]):
            key = f'{line["id"]}-{i}'
            if _ROUTES.get(key, {}).get("ready"):
                continue
            try:
                _build_route(key, r)
            except Exception:
                pass


def start_routes():
    global _started
    if _started:
        return
    _started = True
    _load_cache()
    threading.Thread(target=_worker, daemon=True).start()


def line_routes(line):
    start_routes()
    out = []
    for i, r in enumerate(line.get("routes", [])):
        c = _ROUTES.get(f'{line["id"]}-{i}', {})
        out.append({
            "name": r["name"],
            "color": r.get("color", line["color"]),
            "path": c.get("path", []),
            "stops": c.get("stops", []),
            "ready": bool(c.get("ready")),
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
