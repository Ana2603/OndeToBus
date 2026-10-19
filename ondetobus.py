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
import random
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

# Linhas reais da TNSG (Transportes Nossa Senhora das Graças), operadora de
# Cachoeira do Sul — https://www.tnsg.com.br/index.php?area=linhas
#
# "stops" é a sequência de pontos que forma o traçado da linha no mapa.
# Os nomes dos pontos vêm dos horários oficiais da TNSG; as coordenadas de
# UFSM e Rodoviária foram conferidas, as demais são aproximadas (marcadas
# com # TODO) — ajuste com coordenadas reais copiando do Google Maps
# (clique direito no ponto → clique nas coordenadas pra copiar).
LINES = [
    {
        "id": "011",
        "name": "011 · Noêmia / Santa Helena / UFSM",
        "color": "#FFC94D",
        "stops": [
            {"name": "Hotel União", "lat": -30.020594924095338, "lng": -52.90985759824691},   # TODO: ajustar
            {"name": "Rodoviária", "lat": -30.035882741950257, "lng": -52.91323844533343},    # TODO: ajustar
            {"name": "UFSM · Campus Cachoeira do Sul", "lat": -30.00641943463219, "lng": -52.94003979869694},
            {"name": "5 Esquinas", "lat": -30.034176171465678, "lng": -52.90682109971603},    # TODO: ajustar
        ],
    },
    {
        "id": "031",
        "name": "031 · Cohab / Prado",
        "color": "#3FA796",
        "stops": [
            {"name": "Fenarroz", "lat": -30.04748973039611, "lng": -52.88076382175643},   # TODO: ajustar
            {"name": "Hotel União", "lat": -30.020594924095338, "lng": -52.90985759824691},
            {"name": "Cohab - V. Nova", "lat": -30.029826, "lng": -52.922609},  # TODO: ajustar
            {"name": "Av. Brasil", "lat": -30.01757380772811, "lng": -52.91039396067508},  # TODO: ajustar
            {"name": "Rodoviária", "lat": -30.035882741950257, "lng": -52.91323844533343},
        ],
    },
    {"id": "001", "name": "001 · Quinta", "color": "#5B8DEF", "stops": []},
    {"id": "021", "name": "021 · Promorar", "color": "#E4572E", "stops": []},
    {"id": "041", "name": "041 · Soares P. Verde", "color": "#9B6BD9", "stops": []},
    {"id": "072", "name": "072 · Charqueada / Centro / Prado", "color": "#2FA1D8", "stops": []},
    {"id": "081", "name": "081 · Ponche Verde / Fátima", "color": "#D8A22F", "stops": []},
]

STATUS_LABELS = {
    "no_horario": "No horário",
    "atrasado": "Atrasado",
    "quebrado": "Quebrado",
}

# Ponto de referência para gerar dados de exemplo (Cachoeira do Sul - RS)
CENTER_LAT, CENTER_LNG = -30.0392, -52.8939


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
        # Popula com alguns relatos de exemplo para o mapa não nascer vazio.
        # Se a linha já tem "stops" reais cadastrados, usa o primeiro ponto
        # da rota; senão, gera um ponto aproximado perto do centro da cidade.
        now = time.time()
        for line in LINES:
            if line.get("stops"):
                lat = line["stops"][0]["lat"]
                lng = line["stops"][0]["lng"]
            else:
                lat = CENTER_LAT + random.uniform(-0.01, 0.01)
                lng = CENTER_LNG + random.uniform(-0.01, 0.01)
            conn.execute(
                "INSERT INTO reports (line_id, lat, lng, status, created_at) VALUES (?,?,?,?,?)",
                (line["id"], lat, lng, "no_horario", now - random.randint(30, 400)),
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
    return jsonify(LINES)


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
