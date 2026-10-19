"""
OndeToBus — protótipo de rastreamento colaborativo de ônibus.

Este é um projeto acadêmico (Projeto Integrador I). A localização dos ônibus
é alimentada pelos próprios usuários (rastreamento colaborativo), e a
"recarga de cartão" é SIMULADA — não processa pagamentos reais.

Como rodar:
    pip install -r requirements.txt
    python ondetobus.py
Depois abra http://localhost:5000 no navegador.

Variáveis de ambiente opcionais:
    PORT        — porta em que o servidor escuta (o Render define isso
                  sozinho; localmente usa 5000 se não for definida).
    FLASK_DEBUG — "1" liga o modo debug do Flask (recarrega sozinho,
                  mostra stacktrace no navegador). Deixe desligado (padrão)
                  em produção — é um risco de segurança expor o debugger.
    DB_PATH     — caminho do arquivo SQLite. Só precisa mexer nisso se
                  for usar um Persistent Disk no Render (veja nota mais
                  abaixo sobre disco efêmero).
"""

import os
import sqlite3
import time
import json
import urllib.request
from pathlib import Path
from flask import Flask, jsonify, request, g, render_template, send_from_directory

BASE_DIR = Path(__file__).parent
# Por padrão o banco fica junto do código (BASE_DIR). Se depois você
# adicionar um Persistent Disk no Render (para o banco não "zerar" a
# cada deploy/reinício, já que o disco padrão do Render é efêmero),
# basta apontar a variável de ambiente DB_PATH para o caminho montado
# (ex.: /var/data/onibus.db) — sem precisar mexer no código.
DB_PATH = Path(os.environ.get("DB_PATH", BASE_DIR / "onibus.db"))

app = Flask(__name__)

# Única linha cadastrada: Linha 02 · Noêmia / Santa Helena / UFSM
# (TNSG — Cachoeira do Sul), reproduzida a partir do traçado do app.
#
# "stops" = pontos NOMEADOS da linha (os nomes aparecem como no mapa do app):
#   UFSM → Stok Center → Medianeira → Santa Helena → Centro → Hospital (H)
#
# "path" = pontos do traçado em si (a linha verde que segue as ruas), na
# ordem do percurso. Use "path" para desenhar a polilinha no mapa.
#
# ATENÇÃO: as coordenadas foram estimadas a partir da captura de tela
# (usando a UFSM como âncora), então são APROXIMADAS. Para ficar milimétrico,
# clique com o botão direito no ponto no Google Maps e copie as coordenadas.
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
            {"name": "Hospital", "lat": -30.02538, "lng": -52.91347},
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

# --- Ajuste do traçado às ruas -------------------------------------------
# Os pontos de "path" foram estimados a partir de uma imagem, então ficam
# um pouco fora das ruas. Aqui eles são "encaixados" nas ruas reais usando o
# serviço gratuito OSRM (dados do OpenStreetMap). O resultado fica em cache
# na memória para não gastar banda nem chamar o serviço a cada requisição.
# Se o OSRM estiver fora do ar, usa o traçado bruto de "path" (sem quebrar).
_PATH_CACHE = {}  # line_id -> (timestamp, caminho, deu_certo)
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
    req = urllib.request.Request(
        url, headers={"User-Agent": "OndeToBus/1.0 (projeto academico UFSM)"}
    )
    with urllib.request.urlopen(req, timeout=6) as resp:
        data = json.load(resp)
    if data.get("code") != "Ok" or not data.get(key):
        return None
    flat = []
    for item in data[key]:
        for lng, lat in item["geometry"]["coordinates"]:
            flat.append([lat, lng])
    return flat or None


def snapped_path(line):
    raw = line.get("path") or []
    if len(raw) < 2:
        return raw
    cached = _PATH_CACHE.get(line["id"])
    if cached:
        ts, path, ok = cached
        if ok or time.time() - ts < _RETRY_SECONDS:
            return path
    for kind in ("match", "route"):
        try:
            result = _osrm(kind, raw)
            if result:
                _PATH_CACHE[line["id"]] = (time.time(), result, True)
                return result
        except Exception:
            pass
    _PATH_CACHE[line["id"]] = (time.time(), raw, False)
    return raw


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
        # Popula com um relato de exemplo (no primeiro ponto da linha)
        # para o mapa não nascer vazio.
        now = time.time()
        for line in LINES:
            first = line["stops"][0]
            conn.execute(
                "INSERT INTO reports (line_id, lat, lng, status, created_at) VALUES (?,?,?,?,?)",
                (line["id"], first["lat"], first["lng"], "no_horario", now - 60),
            )
        # Cartão de exemplo para testar a recarga simulada
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
    # Servido na raiz (e não em /static/sw.js) de propósito: o "escopo"
    # de um service worker por padrão é a pasta onde o arquivo está — se
    # ficasse em /static/, ele só controlaria páginas dentro de /static/.
    # Na raiz, ele controla o site inteiro (necessário pro app funcionar
    # offline / abrir "por fora" como app instalado).
    response = send_from_directory(app.static_folder, "sw.js")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Content-Type"] = "application/javascript"
    return response


@app.route("/api/lines")
def api_lines():
    return jsonify([{**l, "path": snapped_path(l)} for l in LINES])


@app.route("/api/status")
def api_status():
    """Retorna a posição/status mais recente de cada linha."""
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

        entry = {
            "id": line["id"],
            "name": line["name"],
            "color": line["color"],
            "stops": line.get("stops", []),
            "path": snapped_path(line),
            "status": row["status"] if row else None,
            "status_label": STATUS_LABELS.get(row["status"]) if row else "Sem relatos",
            "lat": row["lat"] if row else None,
            "lng": row["lng"] if row else None,
            "seconds_ago": int(time.time() - row["created_at"]) if row else None,
            "reporters": reporters,
        }
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
    # DEBUG fica desligado por padrão (produção). Para testar localmente
    # com o modo debug do Flask (recarrega sozinho, mostra stacktrace no
    # navegador), rode com a variável de ambiente: FLASK_DEBUG=1
    modo_debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=porta, debug=modo_debug)
