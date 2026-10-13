"""Radar Airsoft: lee las tiendas, guarda el catálogo y avisa por Telegram.

Dónde vive cada cosa:
  config/ (rama main)   -> lo que decide el usuario: settings.json, watch.json (lo que sigue), bot.json (chat de Telegram)
  data/   (rama data)   -> lo que produce el lector: catálogo, precios, promociones, estado del seguimiento
  live/   (rama live)   -> lecturas en vivo de un producto pedidas desde la web

Uso:
  python scraper/radar.py catalog DATA_DIR            # cada 2 horas
  python scraper/radar.py bot DATA_DIR                # cada 10 minutos
  python scraper/radar.py live URL ID OUT.json        # precio en vivo de un producto
  python scraper/radar.py web OPS_JSON                # cambios hechos desde la web
"""
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from html import escape
from urllib.parse import urljoin, urlsplit, urlencode, parse_qsl, urlunsplit

sys.path.insert(0, os.path.dirname(__file__))

import telegram  # noqa: E402
from extract import (extract_listing, extract_product, extract_promos, listing_prestashop_json,  # noqa: E402
                     next_page_url, norm, short_id, soup_of)
from fetch import Fetcher  # noqa: E402
from stores import STORES  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(ROOT, "config")
SITE_URL = os.environ.get("SITE_URL", "https://jonnyjona.github.io/radar-airsoft/")
try:
    from zoneinfo import ZoneInfo
    MADRID = ZoneInfo("Europe/Madrid")
except Exception:  # pragma: no cover
    MADRID = timezone(timedelta(hours=1))

DEFAULT_SETTINGS = {
    "minDiscount": 20,      # rebajas del catálogo y campañas: avisar desde este %
    "keywords": [],         # palabras que siempre interesan, aunque no lleguen al mínimo
    "notifyPromos": True,   # campañas nuevas en portada
    "notifyDeals": True,    # réplicas del catálogo recién rebajadas
    "notifyDrop": True,     # seguimiento sin condiciones: cualquier bajada
    "notifyStock": True,    # seguimiento: vuelve a haber stock
    "stores": {k: True for k in STORES},
}
SETTINGS_TYPES = {"minDiscount": int, "keywords": list, "notifyPromos": bool, "notifyDeals": bool,
                  "notifyDrop": bool, "notifyStock": bool, "stores": dict}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today():
    return datetime.now(MADRID).strftime("%Y-%m-%d")


def load(folder, name, default):
    try:
        with open(os.path.join(folder, name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save(folder, name, obj, compact=False):
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
        if compact:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(obj, f, ensure_ascii=False, indent=1)
            f.write("\n")


def eur(v):
    return "—" if v is None else f"{v:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def matches_keywords(name, keywords):
    n = norm(name)
    return any(all(t in n for t in norm(k).split()) for k in keywords if norm(k))


def store_of(url):
    host = urlsplit(url).netloc.lower().removeprefix("www.")
    for sid, cfg in STORES.items():
        if urlsplit(cfg["home"]).netloc.lower().removeprefix("www.") == host:
            return sid
    return None


# ---------------------------------------------------------------- configuración (rama main)

def read_config():
    settings = dict(DEFAULT_SETTINGS)
    settings.update(load(CONFIG_DIR, "settings.json", {}))
    settings["stores"] = {**DEFAULT_SETTINGS["stores"], **(settings.get("stores") or {})}
    watch = load(CONFIG_DIR, "watch.json", [])
    bot = load(CONFIG_DIR, "bot.json", {})
    return settings, (watch if isinstance(watch, list) else []), bot


def apply_ops(ops):
    """Aplica cambios sobre config/. Son repetibles: se pueden volver a aplicar sobre una versión más nueva."""
    settings = load(CONFIG_DIR, "settings.json", {})
    watch = load(CONFIG_DIR, "watch.json", [])
    bot = load(CONFIG_DIR, "bot.json", {})
    for op in ops:
        kind = op.get("op")
        if kind == "watch_add":
            item = op["item"]
            old = next((w for w in watch if w["url"] == item["url"]), None)
            if old:
                old.update({k: item[k] for k in ("target", "pct") if k in item})
            else:
                watch.append(item)
        elif kind == "watch_update":
            for w in watch:
                if w["id"] == op["id"]:
                    for k in ("target", "pct"):
                        if k in op:
                            w[k] = op[k]
        elif kind == "watch_remove":
            watch = [w for w in watch if w["id"] != op["id"]]
        elif kind == "settings":
            settings.update(op["values"])
        elif kind == "chat":
            bot["chatId"] = op["chatId"]
    save(CONFIG_DIR, "settings.json", settings)
    save(CONFIG_DIR, "watch.json", watch)
    save(CONFIG_DIR, "bot.json", bot)


def git(*args, check=True):
    return subprocess.run(["git", *args], cwd=ROOT, check=check, capture_output=True, text=True)


def commit_config(ops, message):
    """Guarda los cambios en la rama main. Si otro proceso escribió a la vez, se reintenta sobre lo nuevo."""
    if not ops:
        return True
    if not os.environ.get("GITHUB_ACTIONS"):
        apply_ops(ops)  # en local: solo escribir los archivos
        return True
    for attempt in range(6):
        git("fetch", "-q", "origin", "main")
        git("reset", "-q", "--hard", "origin/main")
        apply_ops(ops)
        git("add", "config")
        if git("diff", "--cached", "--quiet", check=False).returncode == 0:
            return True
        git("-c", "user.name=radar-airsoft[bot]", "-c", "user.email=radar-airsoft@users.noreply.github.com",
            "commit", "-qm", message)
        if git("push", "-q", "origin", "HEAD:main", check=False).returncode == 0:
            return True
        time.sleep(3 + 2 * attempt)
    return False


def chat_id(bot):
    return bot.get("chatId") or os.environ.get("TELEGRAM_CHAT_ID", "").strip() or None


# ---------------------------------------------------------------- lectura de una ficha

def read_product(fx, url):
    html = fx.get(url)
    host = urlsplit(url).netloc
    if not html:
        blocked = fx.blocked.get(host) or str(fx.errors.get(host) or "").startswith("HTTP 403")
        return {"ok": False, "note": "La tienda no deja leer esta página de forma automática" if blocked
                else f"No se pudo leer ({fx.errors.get(host) or 'error'})"}
    info = extract_product(html, url)
    if info["price"] is None:
        return {"ok": False, "name": info.get("name"), "note": "No encontré el precio en la página"}
    return {"ok": True, **info, "at": now_iso()}


def discount_of(price, regular):
    return round(100 * (1 - price / regular)) if price and regular and regular > price else 0


# ---------------------------------------------------------------- catálogo

def page_url(cfg, cat, n):
    if cfg.get("page_path"):
        return cat.rstrip("/") + cfg["page_path"].format(n=n)
    parts = urlsplit(cat)
    q = dict(parse_qsl(parts.query))
    q[cfg["page_param"]] = str(n)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), ""))


def crawl_store(fx, sid, cfg, log):
    items, methods, pages = {}, {}, 0
    if cfg.get("delay"):
        fx.slow[urlsplit(cfg["home"]).netloc] = float(cfg["delay"])
    cats = list(cfg["categories"])
    if cfg.get("discover"):
        for src in (cfg["home"], cfg["categories"][0]):
            html = fx.get(src)
            if not html:
                continue
            soup = soup_of(html)
            for a in soup.find_all("a", href=True):
                href = urljoin(src, a["href"]).split("?")[0]
                if href.startswith(cfg["discover"].rstrip("/")) and re.search(r"-c\d+\.html$", href) and href not in cats:
                    cats.append(href)
        log(f"[{sid}] categorías: {len(cats)}")
    for cat in cats:
        url, n = cat, cfg.get("page_start", 1)
        visited = set()
        for _ in range(cfg.get("max_pages", 20)):
            if url in visited:
                break
            visited.add(url)
            found, soup, method = [], None, "?"
            if cfg.get("prestashop_ajax"):
                txt = fx.get(url, headers={"Accept": "application/json, text/javascript, */*; q=0.01",
                                           "X-Requested-With": "XMLHttpRequest"})
                if txt and txt.lstrip().startswith("{"):
                    try:
                        found, method = listing_prestashop_json(json.loads(txt), url), "prestashop_json"
                    except ValueError:
                        found = []
            if not found:
                html = fx.get(url)
                if not html:
                    break
                found, soup, method = extract_listing(html, url, cfg)
            pages += 1
            new = [it for it in found if it["url"] not in items]
            methods[method] = methods.get(method, 0) + len(new)
            for it in new:
                items[it["url"]] = it
            if not new:
                break
            nxt = next_page_url(soup, url) if soup is not None else None
            n += 1
            url = nxt or page_url(cfg, cat, n)
    host = urlsplit(cfg["home"]).netloc
    info = {"count": len(items), "pages": pages, "methods": methods,
            "blocked": fx.blocked.get(host, 0), "error": fx.errors.get(host)}
    log(f"[{sid}] {len(items)} productos en {pages} páginas · {methods} · robots {info['blocked']} · error {info['error']}")
    return list(items.values()), info


def run_catalog(data_dir):
    settings, watch, bot = read_config()
    state = load(data_dir, "state.json", {})
    old_catalog = load(data_dir, "catalog.json", {"items": [], "stores": {}})
    prices = load(data_dir, "prices.json", {})
    promos = load(data_dir, "promos.json", {"items": []})
    wstate = load(data_dir, "watch-state.json", {})
    first_run = "dealsSeen" not in state
    d = today()
    log = print

    # 1) catálogo: cada tienda en su hilo (las pausas entre peticiones son por tienda)
    old_by_store = {}
    for it in old_catalog.get("items", []):
        old_by_store.setdefault(it["s"], []).append(it)

    def crawl_one(sid):
        try:
            return crawl_store(Fetcher(), sid, STORES[sid], log)
        except Exception as e:  # una tienda rota no debe parar las demás
            log(f"[{sid}] ERROR {type(e).__name__}: {e}")
            return [], {"count": 0, "error": f"{type(e).__name__}: {e}", "blocked": 0}

    active = [sid for sid in STORES if settings["stores"].get(sid, True)]
    with ThreadPoolExecutor(max_workers=len(active) or 1) as pool:
        results = dict(zip(active, pool.map(crawl_one, active)))

    all_items, store_status = [], {}
    for sid, cfg in STORES.items():
        if sid not in results:
            store_status[sid] = {"name": cfg["name"], "status": "off", "note": "Desactivada en ajustes", "count": 0}
            continue
        items, info = results[sid]
        prev = old_by_store.get(sid, [])
        if items:
            status, note = "ok", cfg.get("note")
            if info.get("error"):
                got = {it["url"] for it in items}
                keep = [o for o in prev if o["u"] not in got]
                all_items.extend(keep)
                if keep:
                    note = ((note + " ") if note else "") + f"Lectura parcial: {len(keep)} productos vienen de la lectura anterior."
            for it in items:
                all_items.append({"s": sid, "n": it["name"], "u": it["url"], "p": round(it["price"], 2),
                                  "r": round(it["regular"], 2) if it.get("regular") else None,
                                  "k": None if it.get("inStock") is None else (1 if it["inStock"] else 0)})
            updated = now_iso()
        else:
            all_items.extend(prev)
            if info.get("blocked") or str(info.get("error") or "").startswith("HTTP 403"):
                status, note = "blocked", "La tienda no permite leer su catálogo de forma automática"
            else:
                status, note = "error", f"No se pudo leer ({info.get('error') or 'sin productos'})"
            if prev:
                note += "; se muestran los datos de la última lectura"
            updated = old_catalog.get("stores", {}).get(sid, {}).get("updated")
        store_status[sid] = {"name": cfg["name"], "status": status, "note": note,
                             "count": sum(1 for x in all_items if x["s"] == sid), "updated": updated}

    # 2) historial de precios (un punto por cambio)
    for it in all_items:
        h = prices.setdefault(it["u"], [])
        if not h or h[-1][1] != it["p"]:
            h.append([d, it["p"]])
        del h[:-120]

    # 3) réplicas recién rebajadas en el catálogo
    alerts = []
    seen = set(state.get("dealsSeen", []))
    now_seen, new_deals = set(), []
    for it in all_items:
        pct = discount_of(it["p"], it["r"])
        if not pct:
            continue
        key = f"{it['u']}@{it['p']}"
        now_seen.add(key)
        if key in seen or it["k"] == 0:
            continue
        kw = matches_keywords(it["n"], settings["keywords"])
        if pct >= settings["minDiscount"] or kw:
            new_deals.append((kw, pct, it))
    state["dealsSeen"] = sorted(now_seen)
    if settings["notifyDeals"] and new_deals and not first_run:
        new_deals.sort(key=lambda x: (not x[0], -x[1]))
        lines = [f"🏷️ <b>{len(new_deals)} réplica(s) rebajada(s)</b>"]
        for kw, pct, it in new_deals[:15]:
            lines.append(f"• {'⭐ ' if kw else ''}{escape(STORES[it['s']]['name'])}: <a href=\"{escape(it['u'])}\">{escape(it['n'])}</a> "
                         f"{eur(it['p'])} <s>{eur(it['r'])}</s> (−{pct} %)")
        if len(new_deals) > 15:
            lines.append(f"…y {len(new_deals) - 15} más en la web.")
        alerts.append("\n".join(lines))

    # 4) campañas en portada / páginas de promociones
    by_id = {p["id"]: p for p in promos.get("items", [])}
    read_ok, seen_now, new_promos = set(), set(), []
    fx = Fetcher()
    for sid in active:
        cfg = STORES[sid]
        for url in cfg.get("promo_pages", []):
            html = fx.get(url)
            if not html:
                continue
            read_ok.add(sid)
            for p in extract_promos(html, url, sid, cfg["product_re"]):
                seen_now.add(p["id"])
                if p["id"] in by_id:
                    by_id[p["id"]].update({"lastSeen": d, "active": True, "discount": p["discount"], "url": p["url"]})
                else:
                    p.update({"firstSeen": d, "lastSeen": d, "active": True})
                    by_id[p["id"]] = p
                    new_promos.append(p)
    for p in by_id.values():
        if p["store"] in read_ok and p["id"] not in seen_now:
            p["active"] = False
    cutoff = (datetime.now(MADRID) - timedelta(days=60)).strftime("%Y-%m-%d")
    promos = {"updated": now_iso(), "items": [p for p in by_id.values() if p["active"] or p["lastSeen"] >= cutoff]}
    if settings["notifyPromos"] and new_promos and not first_run:
        relevant = [p for p in new_promos if p["discount"] is None or p["discount"] >= settings["minDiscount"]
                    or matches_keywords(p["title"], settings["keywords"])]
        if relevant:
            lines = ["📣 <b>Campañas nuevas</b>"]
            for p in relevant[:12]:
                pct = f" (−{p['discount']} %)" if p["discount"] else ""
                lines.append(f"• {escape(STORES[p['store']]['name'])}: <a href=\"{escape(p['url'])}\">{escape(p['title'])}</a>{pct}")
            alerts.append("\n".join(lines))

    # 5) artículos en seguimiento
    alerts += check_watch(watch, wstate, settings, d)

    # 6) guardar
    save(data_dir, "catalog.json", {"updated": now_iso(), "stores": store_status, "items": all_items}, compact=True)
    save(data_dir, "prices.json", prices, compact=True)
    save(data_dir, "promos.json", promos)
    save(data_dir, "watch-state.json", wstate)
    save(data_dir, "state.json", state)
    print(f"Total: {len(all_items)} productos, {len(new_deals)} rebajas nuevas, {len(new_promos)} campañas nuevas, "
          f"{len(watch)} en seguimiento, primera vez={first_run}")
    cid = chat_id(bot)
    if alerts and cid:
        telegram.send(cid, "\n\n".join(alerts) + f"\n\n<a href=\"{SITE_URL}\">Abrir Radar Airsoft</a>")


def conditions_text(w):
    parts = []
    if w.get("target"):
        parts.append(f"≤ {eur(w['target'])}")
    if w.get("pct"):
        parts.append(f"≥ {w['pct']} % de descuento")
    return " o ".join(parts) if parts else "cualquier bajada"


def check_watch(watch, wstate, settings, d):
    """Lee cada artículo seguido y decide si avisar."""
    groups = {}
    for w in watch:
        groups.setdefault(w.get("store") or urlsplit(w["url"]).netloc, []).append(w)

    def work(ws):
        fx = Fetcher()
        return [(w["id"], read_product(fx, w["url"])) for w in ws]

    results = {}
    with ThreadPoolExecutor(max_workers=max(1, len(groups))) as pool:
        for part in pool.map(work, groups.values()):
            results.update(dict(part))

    alerts = []
    for w in watch:
        st = wstate.setdefault(w["id"], {"history": []})
        r = results.get(w["id"], {"ok": False, "note": "Sin lectura"})
        if not r["ok"]:
            st.update({"status": "error", "note": r["note"], "lastCheck": now_iso()})
            continue
        first = w.get("first") or {}
        prev_price = st.get("price", first.get("price"))
        prev_stock = st.get("inStock", first.get("inStock"))
        price, regular, stock = r["price"], r["regular"], r["inStock"]
        disc = discount_of(price, regular)
        st.update({"price": price, "regular": regular, "discount": disc, "inStock": stock, "status": "ok",
                   "note": None, "lastCheck": now_iso(), "name": r.get("name") or st.get("name")})
        h = st.setdefault("history", [])
        if h and h[-1][0] == d:
            h[-1][1] = price
        elif not h or h[-1][1] != price:
            h.append([d, price])
        del h[:-365]

        name = escape(w.get("name") or st.get("name") or w["url"])
        store = escape(STORES.get(w.get("store"), {}).get("name", ""))
        link = f"<a href=\"{escape(w['url'])}\">{name}</a>"
        rules = bool(w.get("target") or w.get("pct"))
        hit = (w.get("target") and price <= w["target"]) or (w.get("pct") and disc >= w["pct"])
        sent = False
        if rules and hit and stock is not False:
            last = st.get("alertPrice")
            if last is None or price < last - 0.009:
                extra = f" (antes {eur(regular)}, −{disc} %)" if disc else ""
                alerts.append(f"🎯 <b>Cumple tu aviso</b> ({conditions_text(w)})\n{store}: {link}\n<b>{eur(price)}</b>{extra}")
                st["alertPrice"] = price
                sent = True
        elif rules and not hit:
            st["alertPrice"] = None
        if not rules and settings["notifyDrop"] and prev_price and price < prev_price - 0.009:
            alerts.append(f"📉 <b>Ha bajado de precio</b>\n{store}: {link}\n{eur(prev_price)} → <b>{eur(price)}</b>")
            sent = True
        if settings["notifyStock"] and prev_stock is False and stock is True and not sent:
            alerts.append(f"📦 <b>Vuelve a haber stock</b>\n{store}: {link} a {eur(price)}")
    ids = {w["id"] for w in watch}
    for k in list(wstate):
        if k not in ids:
            del wstate[k]
    return alerts


# ---------------------------------------------------------------- seguir artículos (común a bot y web)

def new_watch_item(url, target=None, pct=None, name=None):
    fx = Fetcher()
    r = read_product(fx, url)
    item = {"id": short_id(url), "url": url, "store": store_of(url), "name": name or r.get("name") or None,
            "target": target, "pct": pct, "addedAt": now_iso()}
    if r["ok"]:
        item["first"] = {"price": r["price"], "regular": r["regular"], "inStock": r["inStock"], "at": r["at"]}
    return item, r


def describe_new(item, r):
    if not r["ok"]:
        return f"Añadido, pero ahora no pude leer el precio: {escape(r['note'])}. Lo intentaré en la próxima lectura."
    stock = "" if r["inStock"] is None else (" · en stock" if r["inStock"] else " · sin stock")
    disc = discount_of(r["price"], r["regular"])
    now = f"{eur(r['price'])}" + (f" (−{disc} %)" if disc else "") + stock
    return f"✅ Siguiendo <b>{escape(item['name'] or item['url'])}</b>\nAhora: {now}\nAviso: {conditions_text(item)}"


def parse_conditions(text):
    target = pct = None
    for tok in re.findall(r"\d+(?:[.,]\d{1,2})?\s*%?", text):
        tok = tok.replace(" ", "")
        if tok.endswith("%"):
            pct = max(1, min(90, int(float(tok[:-1].replace(",", ".")))))
        else:
            target = float(tok.replace(",", "."))
    return target, pct


# ---------------------------------------------------------------- bot de Telegram

HELP = (
    "<b>Radar Airsoft</b> 🎯\n"
    "Te aviso de campañas, réplicas rebajadas y de los artículos que sigues.\n\n"
    "/seguir <i>enlace</i> [precio] [descuento%] — p. ej. <code>/seguir https://… 150 20%</code>\n"
    "/aviso <i>nº</i> [precio] [descuento%] — cambiar el aviso de uno\n"
    "/lista — artículos que sigues\n"
    "/dejar <i>nº</i> — dejar de seguir uno\n"
    "/buscar <i>palabras</i> — los más baratos del catálogo\n"
    "/ofertas — las mayores rebajas ahora\n"
    "/minimo <i>%</i> — descuento mínimo para avisarte de rebajas y campañas\n"
    "/claves <i>a, b</i> — palabras que siempre te interesan (vacío para borrar)\n"
    "/avisos — ver tus ajustes\n"
    "/estado — estado de cada tienda\n\n"
    f"Web: {SITE_URL}"
)


def search_catalog(catalog, query, limit=8):
    toks = [t for t in norm(query).split() if len(t) > 1]
    syn = {"gbb": ["gbb", "gbbr", "blowback"], "gbbr": ["gbb", "gbbr", "blowback"], "aeg": ["aeg", "electric", "electrica"]}
    out = [it for it in catalog.get("items", []) if all(any(s in norm(it["n"]) for s in syn.get(t, [t])) for t in toks)]
    out.sort(key=lambda x: (x["k"] == 0, x["p"]))
    return out[:limit], len(out)


def run_bot(data_dir):
    settings, watch, bot = read_config()
    ups = telegram.updates(0)
    if not ups:
        print("Sin mensajes nuevos")
        return
    catalog = load(data_dir, "catalog.json", {"items": [], "stores": {}})
    wstate = load(data_dir, "watch-state.json", {})
    owner = chat_id(bot)
    ops, replies = [], []
    for u in ups:
        msg = u.get("message") or u.get("edited_message")
        if not msg or "text" not in msg:
            continue
        chat = msg["chat"]["id"]
        if not owner:
            owner = chat
            ops.append({"op": "chat", "chatId": chat})
        if str(chat) != str(owner):
            replies.append((chat, "Este bot es privado."))
            continue
        cmd, _, arg = msg["text"].strip().partition(" ")
        cmd, arg = cmd.split("@")[0].lower(), arg.strip()
        reply = None
        if cmd in ("/start", "/ayuda", "/help"):
            reply = HELP
        elif cmd == "/seguir":
            m = re.search(r"https?://\S+", arg)
            if not m:
                reply = "Pásame el enlace del producto, por ejemplo:\n<code>/seguir https://… 150 20%</code>"
            else:
                url = m.group(0).rstrip(").,")
                target, pct = parse_conditions(arg.replace(m.group(0), ""))
                if not store_of(url):
                    reply = "Ese enlace no es de ninguna de las tiendas que sigo."
                else:
                    item, r = new_watch_item(url, target, pct)
                    old = next((w for w in watch if w["url"] == url), None)
                    if old:
                        old.update({"target": target, "pct": pct})
                    else:
                        watch.append(item)
                    ops.append({"op": "watch_add", "item": item})
                    reply = describe_new(item, r) if not old else f"Ya lo seguías. Aviso actualizado: {conditions_text(old)}"
        elif cmd == "/aviso":
            num, _, rest = arg.partition(" ")
            try:
                w = watch[int(num) - 1]
                target, pct = parse_conditions(rest)
                w.update({"target": target, "pct": pct})
                ops.append({"op": "watch_update", "id": w["id"], "target": target, "pct": pct})
                reply = f"Aviso de {escape(w.get('name') or w['url'])}: {conditions_text(w)}"
            except (ValueError, IndexError):
                reply = "Ejemplo: <code>/aviso 2 150 20%</code> (número de /lista, precio y/o descuento)"
        elif cmd == "/lista":
            if not watch:
                reply = "No sigues ningún artículo. Usa /seguir enlace"
            else:
                lines = ["<b>Artículos que sigues</b>"]
                for i, w in enumerate(watch, 1):
                    st = wstate.get(w["id"], {})
                    price = st.get("price", (w.get("first") or {}).get("price"))
                    lines.append(f"{i}. <a href=\"{escape(w['url'])}\">{escape(w.get('name') or st.get('name') or w['url'])}</a> — "
                                 f"{eur(price)} · aviso {conditions_text(w)}")
                reply = "\n".join(lines)
        elif cmd == "/dejar":
            try:
                w = watch.pop(int(arg) - 1)
                ops.append({"op": "watch_remove", "id": w["id"]})
                reply = f"Dejo de seguir {escape(w.get('name') or w['url'])}"
            except (ValueError, IndexError):
                reply = "Dime el número de /lista, por ejemplo: /dejar 2"
        elif cmd == "/minimo":
            try:
                settings["minDiscount"] = max(0, min(90, int(float(arg.replace("%", "").replace(",", ".")))))
                ops.append({"op": "settings", "values": {"minDiscount": settings["minDiscount"]}})
                reply = f"Te avisaré de rebajas y campañas desde un {settings['minDiscount']} %."
            except ValueError:
                reply = "Ejemplo: /minimo 25"
        elif cmd == "/claves":
            settings["keywords"] = [k.strip().lower() for k in arg.split(",") if k.strip()]
            ops.append({"op": "settings", "values": {"keywords": settings["keywords"]}})
            reply = ("Palabras clave: " + ", ".join(settings["keywords"])) if settings["keywords"] else "Palabras clave borradas."
        elif cmd == "/avisos":
            yn = lambda v: "sí" if v else "no"  # noqa: E731
            reply = (f"Rebajas y campañas desde: {settings['minDiscount']} %\n"
                     f"Palabras clave: {', '.join(settings['keywords']) or '—'}\n"
                     f"Réplicas rebajadas: {yn(settings['notifyDeals'])} · Campañas: {yn(settings['notifyPromos'])}\n"
                     f"Seguimiento sin condiciones, cualquier bajada: {yn(settings['notifyDrop'])} · Vuelta de stock: {yn(settings['notifyStock'])}")
        elif cmd == "/buscar":
            if not arg:
                reply = "Ejemplo: /buscar noveske gbb n4"
            else:
                res, total = search_catalog(catalog, arg)
                if not res:
                    reply = f"No hay nada en el catálogo con «{escape(arg)}»."
                else:
                    lines = [f"<b>{total} resultado(s) para «{escape(arg)}»</b>"]
                    for it in res:
                        ss = " · sin stock" if it["k"] == 0 else ""
                        lines.append(f"• {eur(it['p'])} — {escape(STORES[it['s']]['name'])}: <a href=\"{escape(it['u'])}\">{escape(it['n'])}</a>{ss}")
                    reply = "\n".join(lines)
        elif cmd == "/ofertas":
            deals = [it for it in catalog.get("items", []) if discount_of(it["p"], it.get("r")) and it["k"] != 0]
            deals.sort(key=lambda it: -discount_of(it["p"], it["r"]))
            lines = ["<b>Mayores rebajas ahora</b>"]
            for it in deals[:10]:
                lines.append(f"• −{discount_of(it['p'], it['r'])} % {escape(STORES[it['s']]['name'])}: "
                             f"<a href=\"{escape(it['u'])}\">{escape(it['n'])}</a> {eur(it['p'])}")
            reply = "\n".join(lines) if deals else "Todavía no hay rebajas en el catálogo."
        elif cmd == "/estado":
            lines = [f"<b>Última lectura</b>: {catalog.get('updated', '—')}"]
            for sid, s in catalog.get("stores", {}).items():
                lines.append(f"• {escape(s.get('name', sid))}: {s.get('status')} · {s.get('count', 0)} productos")
            reply = "\n".join(lines)
        else:
            reply = "No entiendo ese mensaje.\n\n" + HELP
        replies.append((chat, reply))
    saved = commit_config(ops, "Cambios desde Telegram")
    telegram.confirm(ups[-1]["update_id"] + 1)  # Telegram olvida los mensajes ya procesados
    for chat, text in replies:
        if ops and not saved and str(chat) == str(owner):
            text += "\n\n⚠️ No pude guardar el cambio. Inténtalo otra vez en unos minutos."
        telegram.send(chat, text)


# ---------------------------------------------------------------- precio en vivo de un producto

def run_live(url, req_id, out_path):
    fx = Fetcher()
    r = read_product(fx, url) if store_of(url) else {"ok": False, "note": "Ese enlace no es de ninguna tienda del radar"}
    r.update({"id": req_id, "url": url, "finishedAt": now_iso(), "discount": discount_of(r.get("price"), r.get("regular"))})
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False)
    print(json.dumps(r, ensure_ascii=False))


# ---------------------------------------------------------------- cambios desde la web

def run_web(raw_ops):
    ops_in = json.loads(raw_ops)
    if not isinstance(ops_in, list) or len(ops_in) > 20:
        sys.exit("Formato de cambios no válido")
    settings, watch, bot = read_config()
    ops, notes = [], []
    for op in ops_in:
        kind = op.get("op")
        num = lambda v, hi: (None if v in (None, "") else max(0, min(hi, float(v))))  # noqa: E731
        if kind == "watch_add":
            url = str(op.get("url", ""))
            if not re.match(r"^https://", url) or not store_of(url):
                notes.append(f"Enlace no válido: {url}")
                continue
            target = num(op.get("target"), 100000)
            pct = num(op.get("pct"), 90)
            item, r = new_watch_item(url, target, int(pct) if pct else None, str(op.get("name") or "")[:200] or None)
            ops.append({"op": "watch_add", "item": item})
            notes.append(describe_new(item, r))
        elif kind == "watch_update" and isinstance(op.get("id"), str):
            target = num(op.get("target"), 100000)
            pct = num(op.get("pct"), 90)
            ops.append({"op": "watch_update", "id": op["id"], "target": target, "pct": int(pct) if pct else None})
        elif kind == "watch_remove" and isinstance(op.get("id"), str):
            ops.append({"op": "watch_remove", "id": op["id"]})
        elif kind == "settings" and isinstance(op.get("values"), dict):
            vals = {}
            for k, v in op["values"].items():
                t = SETTINGS_TYPES.get(k)
                if t is int:
                    vals[k] = max(0, min(90, int(v)))
                elif t is bool:
                    vals[k] = bool(v)
                elif t is list:
                    vals[k] = [str(x).strip().lower()[:60] for x in v if str(x).strip()][:30]
                elif t is dict:
                    vals[k] = {s: bool(v.get(s, True)) for s in STORES}
            ops.append({"op": "settings", "values": vals})
    ok = commit_config(ops, "Cambios desde la web")
    print("Guardado" if ok else "No se pudo guardar", ops)
    cid = chat_id(bot)
    adds = [n for n in notes if n.startswith(("✅", "Añadido"))]
    if ok and cid and adds:
        telegram.send(cid, "\n\n".join(adds) + "\n\n<i>(desde la web)</i>")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "catalog"
    t0 = time.time()
    if mode == "catalog":
        run_catalog(sys.argv[2] if len(sys.argv) > 2 else "data")
    elif mode == "bot":
        run_bot(sys.argv[2] if len(sys.argv) > 2 else "data")
    elif mode == "live":
        run_live(sys.argv[2], sys.argv[3], sys.argv[4])
    elif mode == "web":
        run_web(sys.argv[2])
    else:
        sys.exit(f"Modo desconocido: {mode}")
    print(f"Hecho en {time.time() - t0:.0f} s")
