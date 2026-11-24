"""
OndeToBus — protótipo de rastreamento colaborativo de ônibus (Projeto Integrador I).
A recarga de cartão é SIMULADA.

Rodar:  pip install flask  &&  python ondetobus.py   ->  http://localhost:5000
Variáveis opcionais: PORT, FLASK_DEBUG ("1" liga debug), DB_PATH.
"""

import os
import sqlite3
import time
import json, math, hashlib
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
            {"code": "002A", "name": "002A · UFSM → Rodoviária → HCB", "color": "#1E6FE0", "addresses": UFSM_HCB},
            {"code": "002B", "name": "002B · HCB → Rodoviária → UFSM", "color": "#E8590C", "addresses": HCB_UFSM},
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
CACHE_V = 3  # mude para forçar recalcular as rotas


def _load_cache():
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        if data.get("v") != CACHE_V:
            return
        _GEO.update(data.get("geo", {}))
        _ROUTES.update(data.get("routes", {}))
    except Exception:
        pass


def _save_cache():
    try:
        CACHE_FILE.write_text(json.dumps({"v": CACHE_V, "geo": _GEO, "routes": _ROUTES}), encoding="utf-8")
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


def _photon(q):
    url = "https://photon.komoot.io/api/?" + urllib.parse.urlencode(
        {"q": q, "lat": -30.03, "lon": -52.9, "limit": 3})
    data = _get(url)
    time.sleep(0.4)
    for f in data.get("features", []):
        lon, lat = f["geometry"]["coordinates"]
        if -30.07 < lat < -29.99 and -52.97 < lon < -52.86:
            return [lat, lon]
    return None


def _try(fn, *args):
    for _ in range(2):
        try:
            return fn(*args)
        except Exception:
            time.sleep(3)
    return None


UFSM_FALLBACK = [-30.01397, -52.93531]


def geocode(addr):
    if _GEO.get(addr):
        return _GEO[addr]
    if addr.startswith("UFSM"):
        res = _try(_nominatim, {"q": "Universidade Federal de Santa Maria, Cachoeira do Sul"})
        _GEO[addr] = res or UFSM_FALLBACK
        return _GEO[addr]
    rua, _, num = addr.partition(",")
    rua = rua.replace("Lib.", "Libero").strip()
    num = num.strip()
    base = {"city": "Cachoeira do Sul", "state": "Rio Grande do Sul", "country": "Brasil"}
    res = None
    if num:
        res = _try(_nominatim, {**base, "street": f"{num} {rua}"})
    if not res:
        res = _try(_photon, f"{rua} {num}, Cachoeira do Sul")
    if not res:
        res = _try(_nominatim, {**base, "street": rua})
    _GEO[addr] = res
    return res


def _dist_km(a, b):
    import math
    dy = (a[0] - b[0]) * 111.0
    dx = (a[1] - b[1]) * 111.0 * math.cos(math.radians(a[0]))
    return math.hypot(dx, dy)


def _limpar(stops):
    """Remove paradas geocodificadas no lugar errado (picos que vão longe e voltam)."""
    while len(stops) > 2:
        worst, wv = None, 1.2
        for i in range(1, len(stops) - 1):
            a, b, c = stops[i - 1]["pt"], stops[i]["pt"], stops[i + 1]["pt"]
            det = _dist_km(a, b) + _dist_km(b, c) - _dist_km(a, c)
            if det > wv:
                worst, wv = i, det
        if worst is None:
            break
        del stops[worst]
    while len(stops) > 2 and _dist_km(stops[-1]["pt"], stops[-2]["pt"]) > 2.5:
        del stops[-1]
    while len(stops) > 2 and _dist_km(stops[0]["pt"], stops[1]["pt"]) > 2.5:
        del stops[0]
    return stops


def _build_route(key, route):
    stops = []
    for addr in route["addresses"]:
        pt = geocode(addr)
        if not pt:
            continue
        if stops and stops[-1]["pt"] == pt:                # mesmo ponto repetido
            continue
        stops.append({"label": addr, "pt": pt})
    _save_cache()
    stops = _limpar(stops)
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
            dr = _drawn()
            if key in dr and key not in {v.get("frame") for v in dr.values()}:
                continue                     # já tem rota gravada: não perde tempo geocodificando
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


# --- Trechos a remover (laços / ganchos) ----------------------------------
# Locais marcados no mapa onde o traçado entra numa rua lateral e volta
# (por causa de paradas geocodificadas em ruas laterais). Em cada local o
# servidor procura o desvio (um trecho que sai da rua principal e volta perto
# do mesmo ponto) e o apaga, ligando direto os dois lados.
# lat/lng = centro da marcação, r = raio em metros.
LIMPAR_TRECHOS = [
    {"nome": "gancho Rodovia/BR-153", "lat": -30.006059, "lng": -52.932795, "r": 161},
    {"nome": "laco Noemia", "lat": -30.007971, "lng": -52.93111, "r": 230},
    {"nome": "laco Drews (praca)", "lat": -30.015152, "lng": -52.920299, "r": 206},
    {"nome": "desvio Tibirica", "lat": -30.017198, "lng": -52.92491, "r": 257},
    {"nome": "laco Rio Branco", "lat": -30.021845, "lng": -52.913479, "r": 283},
    {"nome": "gancho Goncalves", "lat": -30.02109, "lng": -52.91778, "r": 189},
]
_LIMPO = {}  # cache: (chave, tamanho, 1º ponto, último ponto) -> (path, stops)


def _xy(p, lat0, lng0):
    import math
    return ((p[1] - lng0) * 111320.0 * math.cos(math.radians(lat0)), (p[0] - lat0) * 111320.0)


def _tirar_desvios(path, spots=None, max_iter=2):
    """Apaga, perto de cada local marcado, o desvio de maior 'sobra' (trecho
    muito mais longo que a distância em linha reta entre sua entrada e saída)."""
    import math
    spots = LIMPAR_TRECHOS if spots is None else spots
    p = [list(q) for q in path]
    if len(p) < 6:
        return p
    for sp in spots:
        for _ in range(max_iter):
            n = len(p)
            lat0, lng0 = sp["lat"], sp["lng"]
            xy = [_xy(q, lat0, lng0) for q in p]
            cum = [0.0]
            for i in range(1, n):
                cum.append(cum[-1] + math.dist(xy[i - 1], xy[i]))
            R = sp["r"]
            dist_c = [math.hypot(x, y) for x, y in xy]
            idx = [i for i in range(n) if dist_c[i] <= R + 40]
            best = None
            for a, i in enumerate(idx):
                for j in idx[a + 3:]:
                    d = math.dist(xy[i], xy[j])
                    L = cum[j] - cum[i]
                    if d > 200 or L > 700 or L < d + 90 or L < 1.8 * d:
                        continue
                    if sum(1 for k in range(i + 1, j) if dist_c[k] <= R) < 3:
                        continue
                    key = (round((L - d) / 15.0), -L)
                    if best is None or key > best[0]:
                        best = (key, i, j)
            if best is None:
                break
            _, i, j = best
            p = p[:i + 1] + p[j:]
    return p


def _nearest_on(path, pt):
    best, bd = None, 1e18
    for q in path:
        d = _dist_km(q, pt)
        if d < bd:
            best, bd = q, d
    return best, bd * 1000.0


def _limpar_rota(key, c):
    """Aplica _tirar_desvios ao traçado e reposiciona paradas que ficaram fora."""
    path = c.get("path", [])
    if len(path) < 6 or not c.get("ready"):
        return path, c.get("stops", [])
    ck = (key, len(path), tuple(path[0]), tuple(path[-1]))
    if ck in _LIMPO:
        return _LIMPO[ck]
    clean = _tirar_desvios(path)
    stops = []
    for s in c.get("stops", []):
        pt = [s["lat"], s["lng"]]
        near_spot = any(_dist_km(pt, [sp["lat"], sp["lng"]]) * 1000 <= sp["r"] + 60 for sp in LIMPAR_TRECHOS)
        if near_spot:
            q, d = _nearest_on(clean, pt)
            if q is not None and 30 < d < 300:       # parada ficou fora da rota: encosta nela
                s = {**s, "lat": q[0], "lng": q[1]}
        stops.append(s)
    _LIMPO.clear() if len(_LIMPO) > 20 else None
    _LIMPO[ck] = (clean, stops)
    return clean, stops



# --- Rotas gravadas (traçadas a partir das fotos do app) --------------------
# rotas_desenhadas.json (raiz do projeto): "02-0" = 002A (UFSM → HCB), "02-1" = 002B (HCB → UFSM).
# Carregam na hora, sem geocodificar endereço nem chamar o OSRM para desenhar.
# "snap": o servidor ainda alinha/encaixa nas ruas em segundo plano (e o mapa se atualiza sozinho).
# "frame": rotas com a mesma moldura recebem o mesmo deslocamento de alinhamento.
DRAWN_COMMITTED = BASE_DIR / "rotas_desenhadas.json"
_DRAWN = {"sig": None, "data": {}}


def _drawn():
    sig = []
    for f in (DRAWN_COMMITTED,):
        try:
            st = f.stat()
            sig.append((st.st_mtime_ns, st.st_size))
        except OSError:
            sig.append(None)
    if sig != _DRAWN["sig"]:
        data = {}
        for f in (DRAWN_COMMITTED,):
            try:
                data.update(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                pass
        _DRAWN["sig"], _DRAWN["data"] = sig, data
    return _DRAWN["data"]


# ---- encaixe automático de rotas desenhadas ("snap": true) nas ruas, em segundo plano ----
_SNAP = {}          # chave -> {"sig": str, "path": [...] | None}
_SNAP_FILE = DB_PATH.parent / "snap_cache.json"


def _hav(a, b):
    return 111320 * math.hypot(b[0] - a[0], (b[1] - a[1]) * math.cos(math.radians(a[0])))


def _densify(path, step=25):
    out = [path[0]]
    for a, b in zip(path, path[1:]):
        n = max(1, int(_hav(a, b) // step))
        for k in range(1, n + 1):
            out.append([a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n])
    return out


def _dist_to(path, pt):
    best = 1e12
    for a, b in zip(path, path[1:]):
        k = math.cos(math.radians(pt[0]))
        ax, ay, bx, by, px, py = a[1] * k, a[0], b[1] * k, b[0], pt[1] * k, pt[0]
        dx, dy = bx - ax, by - ay
        t = 0 if dx == dy == 0 else max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
        best = min(best, 111320 * math.hypot(px - (ax + t * dx), py - (ay + t * dy)))
    return best


def _length(p):
    return sum(_hav(a, b) for a, b in zip(p, p[1:]))


def _align(path, old, span=900, step=15):
    """Desliza a rota desenhada (sem girar nem mudar o tamanho) até ela encaixar nas ruas da rota
    automática (que vem do OSRM, ou seja, está em cima das ruas reais). Só confia nos trechos
    parecidos: a média considera os 55% de pontos mais próximos."""
    lat0 = path[0][0]
    ky, kx = 111320.0, 111320.0 * math.cos(math.radians(lat0))
    ref = _densify(old, 10)
    cell = 30
    grid = {}
    for q in ref:
        grid.setdefault((int(q[1] * kx // cell), int(q[0] * ky // cell)), []).append((q[1] * kx, q[0] * ky))
    mine = [(q[1] * kx, q[0] * ky) for q in _densify(path, 40)]

    def cost(dx, dy):
        ds = []
        for x, y in mine:
            x += dx; y += dy
            cx, cy = int(x // cell), int(y // cell)
            best = 1e9
            for gx in range(cx - 3, cx + 4):
                for gy in range(cy - 3, cy + 4):
                    for rx, ry in grid.get((gx, gy), ()):
                        d = (rx - x) ** 2 + (ry - y) ** 2
                        if d < best:
                            best = d
            ds.append(min(math.sqrt(best), 100.0))
        ds.sort()
        k = max(1, int(len(ds) * 0.55))
        return sum(ds[:k]) / k + 0.004 * math.hypot(dx, dy)

    best = (cost(0, 0), 0, 0)
    for r, st in ((span, step), (step * 2, 4)):
        c0 = (best[1], best[2])
        for i in range(-int(r // st), int(r // st) + 1):
            for j in range(-int(r // st), int(r // st) + 1):
                dx, dy = c0[0] + i * st, c0[1] + j * st
                c = cost(dx, dy)
                if c < best[0]:
                    best = (c, dx, dy)
    c, dx, dy = best
    print(f"alinhamento: desloca {dx:.0f} m (leste) {dy:.0f} m (norte); erro médio {c:.1f} m")
    return [[q[0] + dy / ky, q[1] + dx / kx] for q in path], c


_SHIFT = {}                 # chave da "moldura" -> {"sig", "d": (dlat, dlng) | None}
_LOCK = threading.Lock()


def _md5(obj):
    return hashlib.md5(json.dumps(obj).encode()).hexdigest()


def _cache_read():
    try:
        return json.loads(_SNAP_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _cache_put(k, v):
    try:
        c = _cache_read()
        c[k] = v
        _SNAP_FILE.write_text(json.dumps(c), encoding="utf-8")
    except Exception:
        pass


def _shift_for(fkey, fpath):
    """Quanto deslocar o desenho (lat, lng) para encaixar nas ruas reais.
    Calculado uma vez com a rota automática (OSRM) da moldura; vale para todas as rotas dela."""
    sig = _md5(fpath)
    with _LOCK:
        c = _SHIFT.get(fkey) or _cache_read().get("shift:" + fkey)
        if c and c.get("sig") == sig:
            _SHIFT[fkey] = c
            return c.get("d")
        for _ in range(60):                       # espera a rota automática ficar pronta (até ~2 min)
            if _ROUTES.get(fkey, {}).get("ready"):
                break
            time.sleep(2)
        old = _ROUTES.get(fkey, {}).get("path") or []
        d = None
        if len(old) > 5:
            moved, err = _align(fpath, old)
            if err <= 25:
                d = [moved[0][0] - fpath[0][0], moved[0][1] - fpath[0][1]]
            else:
                print("alinhamento ruim, mantendo posição original")
            c = {"sig": sig, "d": d}
            _SHIFT[fkey] = c
            _cache_put("shift:" + fkey, c)
        return d


def _publish(key, sig, path):
    rev = (_SNAP.get(key) or {}).get("rev", 0) + 1
    _SNAP[key] = {"sig": sig, "path": path, "rev": rev, "busy": True}
    return rev


def _snap_job(key, d, sig):
    drawn = _drawn()
    path = d["path"]
    try:
        fkey = d.get("frame") or key
        fpath = (drawn.get(fkey) or d)["path"]
        shift = _shift_for(fkey, fpath)
        if shift:
            path = [[a + shift[0], b + shift[1]] for a, b in path]
            _publish(key, sig, path)              # já mostra o desenho alinhado
        raw = _densify(path)
        out = []
        for i in range(0, len(raw) - 1, 80):
            chunk = raw[i:i + 81]
            if len(chunk) < 2:
                break
            coords = ";".join(f"{p[1]:.6f},{p[0]:.6f}" for p in chunk)
            url = ("https://router.project-osrm.org/match/v1/driving/" + coords +
                   "?geometries=geojson&overview=full&gaps=ignore&radiuses=" + ";".join(["45"] * len(chunk)))
            j = _get(url)
            if j.get("code") != "Ok":
                raise ValueError(j.get("code"))
            for m in j["matchings"]:
                out.extend([c[1], c[0]] for c in m["geometry"]["coordinates"])
        # conferência: só vale se ficou perto do desenho e sem desvios longos
        ok = len(out) > 2 and 0.85 <= _length(out) / max(_length(raw), 1) <= 1.3
        if ok:
            ok = max(_dist_to(raw, q) for q in out[::3]) <= 90
        final = out if ok else path
    except Exception as e:
        print("encaixe falhou:", key, e)
        final = path
    rev = (_SNAP.get(key) or {}).get("rev", 0) + 1
    _SNAP[key] = {"sig": sig, "path": final, "rev": rev, "busy": False}
    _cache_put(key, {"sig": sig, "path": final, "rev": rev})


def _snapped(key, d):
    """(caminho, revisão). Mostra o desenho na hora; o encaixe nas ruas chega depois, em segundo plano."""
    sig = _md5(d["path"])
    st = _SNAP.get(key)
    if st is None:
        c = _cache_read().get(key)
        if c and c.get("sig") == sig:
            st = _SNAP[key] = {**c, "busy": False}
    if st and st.get("sig") == sig:
        return st["path"], st.get("rev", 0)
    _SNAP[key] = {"sig": sig, "path": d["path"], "rev": 0, "busy": True}
    threading.Thread(target=_snap_job, args=(key, d, sig), daemon=True).start()
    return d["path"], 0


def line_routes(line):
    start_routes()
    out = []
    for i, r in enumerate(line.get("routes", [])):
        key = f'{line["id"]}-{i}'
        c = _ROUTES.get(key, {})
        d = _drawn().get(key)
        rev = 0
        if d and len(d.get("path", [])) >= 2:                 # rota gravada em rotas_desenhadas.json
            c = {"ready": True}
            stops = d.get("stops", [])
            if d.get("snap"):
                path, rev = _snapped(key, d)
                if len(stops) >= 2:                           # as paradas acompanham as pontas da rota
                    stops = [dict(stops[0], lat=path[0][0], lng=path[0][1]),
                             dict(stops[-1], lat=path[-1][0], lng=path[-1][1])]
            else:
                path = d["path"]
        else:
            path, stops = _limpar_rota(key, c)
        out.append({
            "name": r["name"],
            "code": r.get("code", ""),
            "color": r.get("color", line["color"]),
            "path": path,
            "stops": stops,
            "ready": bool(c.get("ready")),
            "rev": rev,
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
