"""Radar Airsoft: lee las tiendas, actualiza los datos de la web y avisa por Telegram.

Uso:
  python scraper/radar.py catalog DATA_DIR   # catálogo, promos y seguimiento (cada pocas horas)
  python scraper/radar.py bot DATA_DIR       # responde a los comandos de Telegram (cada pocos minutos)
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from html import escape
from urllib.parse import urljoin, urlsplit, urlencode, parse_qsl, urlunsplit

sys.path.insert(0, os.path.dirname(__file__))

import telegram  # noqa: E402
from extract import (extract_listing, extract_product, extract_promos, listing_prestashop_json,  # noqa: E402
                     next_page_url, norm, short_id, soup_of)
from fetch import Fetcher  # noqa: E402
from stores import STORES  # noqa: E402

SITE_URL = os.environ.get("SITE_URL", "https://jonnyjona.github.io/radar-airsoft/")
MADRID = timezone(timedelta(hours=2))  # aproximado; solo se usa para fechas legibles

DEFAULT_SETTINGS = {
    "minDiscount": 20,
    "keywords": [],
    "notifyPromos": True,
    "notifyDeals": True,
    "notifyDrop": True,
    "notifyTarget": True,
    "notifyStock": True,
    "stores": {k: True for k in STORES},
}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today():
    return datetime.now(MADRID).strftime("%Y-%m-%d")


def load(data_dir, name, default):
    path = os.path.join(data_dir, name)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save(data_dir, name, obj, compact=False):
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, name)
    with open(path, "w", encoding="utf-8") as f:
        if compact:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(obj, f, ensure_ascii=False, indent=1)


def eur(v):
    return "—" if v is None else f"{v:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def settings_of(data_dir):
    s = dict(DEFAULT_SETTINGS)
    s.update(load(data_dir, "settings.json", {}))
    s["stores"] = {**DEFAULT_SETTINGS["stores"], **(s.get("stores") or {})}
    return s


def matches_keywords(name, keywords):
    n = norm(name)
    return any(all(t in n for t in norm(k).split()) for k in keywords if norm(k))


def page_url(cfg, cat, n):
    if cfg.get("page_path"):
        return cat.rstrip("/") + cfg["page_path"].format(n=n)
    parts = urlsplit(cat)
    q = dict(parse_qsl(parts.query))
    q[cfg["page_param"]] = str(n)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), ""))


# ---------------------------------------------------------------- catálogo

def crawl_store(fx, sid, cfg, log):
    items, methods, pages = {}, {}, 0
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
            found = []
            if cfg.get("prestashop_ajax"):
                txt = fx.get(url, headers={"Accept": "application/json, text/javascript, */*; q=0.01",
                                           "X-Requested-With": "XMLHttpRequest"})
                if txt and txt.lstrip().startswith("{"):
                    try:
                        found = listing_prestashop_json(json.loads(txt), url)
                        soup, method = None, "prestashop_json"
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
    info = {
        "count": len(items),
        "pages": pages,
        "methods": methods,
        "blocked": fx.blocked.get(host, 0),
        "error": fx.errors.get(host),
    }
    log(f"[{sid}] {len(items)} productos en {pages} páginas · métodos {methods} · bloqueadas por robots {info['blocked']} · error {info['error']}")
    return list(items.values()), info


def run_catalog(data_dir):
    fx = Fetcher()
    settings = settings_of(data_dir)
    state = load(data_dir, "state.json", {})
    old_catalog = load(data_dir, "catalog.json", {"items": [], "stores": {}})
    prices = load(data_dir, "prices.json", {})
    promos = load(data_dir, "promos.json", {"items": []})
    tracked = load(data_dir, "tracked.json", [])
    first_run = "dealsSeen" not in state
    d = today()
    log = print

    # 1) catálogo
    all_items, store_status = [], {}
    old_by_store = {}
    for it in old_catalog.get("items", []):
        old_by_store.setdefault(it["s"], []).append(it)
    for sid, cfg in STORES.items():
        if not settings["stores"].get(sid, True):
            store_status[sid] = {"name": cfg["name"], "status": "off", "note": "Desactivada en ajustes", "updated": now_iso()}
            continue
        try:
            items, info = crawl_store(fx, sid, cfg, log)
        except Exception as e:  # una tienda rota no debe parar las demás
            items, info = [], {"count": 0, "error": f"{type(e).__name__}: {e}", "blocked": 0}
            log(f"[{sid}] ERROR {info['error']}")
        if items:
            status, note = "ok", cfg.get("note")
            for it in items:
                all_items.append({"s": sid, "n": it["name"], "u": it["url"], "p": round(it["price"], 2),
                                  "r": round(it["regular"], 2) if it.get("regular") else None,
                                  "k": None if it.get("inStock") is None else (1 if it["inStock"] else 0)})
        else:
            prev = old_by_store.get(sid, [])
            all_items.extend(prev)
            if info.get("blocked") or str(info.get("error") or "").startswith("HTTP 403"):
                status, note = "blocked", "La tienda no permite leer su catálogo de forma automática"
            else:
                status, note = "error", f"No se pudo leer ({info.get('error') or 'sin productos'})"
            if prev:
                note += "; se muestran los datos de la última lectura"
        store_status[sid] = {"name": cfg["name"], "status": status, "note": note, "count": len(items) or len(old_by_store.get(sid, [])),
                             "updated": now_iso() if items else (old_catalog.get("stores", {}).get(sid, {}).get("updated")),
                             "debug": info}

    # 2) historial de precios (solo cuando cambia)
    for it in all_items:
        h = prices.setdefault(it["u"], [])
        if not h or h[-1][1] != it["p"]:
            h.append([d, it["p"]])
        if len(h) > 120:
            del h[:-120]

    # 3) rebajas nuevas
    alerts = []
    seen = set(state.get("dealsSeen", []))
    now_seen = set()
    new_deals = []
    for it in all_items:
        if it["r"] and it["r"] > it["p"]:
            pct = round(100 * (1 - it["p"] / it["r"]))
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
        lines = [f"🏷️ <b>{len(new_deals)} rebaja(s) nueva(s)</b>"]
        for kw, pct, it in new_deals[:15]:
            star = "⭐ " if kw else ""
            lines.append(f"• {star}{escape(STORES[it['s']]['name'])}: <a href=\"{escape(it['u'])}\">{escape(it['n'])}</a> "
                         f"{eur(it['p'])} <s>{eur(it['r'])}</s> (−{pct} %)")
        if len(new_deals) > 15:
            lines.append(f"…y {len(new_deals) - 15} más en la web.")
        alerts.append("\n".join(lines))

    # 4) promociones de portada
    by_id = {p["id"]: p for p in promos.get("items", [])}
    read_ok = set()
    new_promos = []
    for sid, cfg in STORES.items():
        if not settings["stores"].get(sid, True):
            continue
        got_any = False
        for url in cfg.get("promo_pages", []):
            html = fx.get(url)
            if not html:
                continue
            got_any = True
            for p in extract_promos(html, url, sid, cfg["product_re"]):
                if p["id"] in by_id:
                    by_id[p["id"]].update({"lastSeen": d, "active": True, "discount": p["discount"], "url": p["url"]})
                else:
                    p.update({"firstSeen": d, "lastSeen": d, "active": True})
                    by_id[p["id"]] = p
                    new_promos.append(p)
        if got_any:
            read_ok.add(sid)
    for p in by_id.values():
        if p["store"] in read_ok and p["lastSeen"] != d:
            p["active"] = False
    # olvidar promos inactivas de hace más de 60 días
    cutoff = (datetime.now(MADRID) - timedelta(days=60)).strftime("%Y-%m-%d")
    promos = {"updated": now_iso(), "items": [p for p in by_id.values() if p["active"] or p["lastSeen"] >= cutoff]}
    if settings["notifyPromos"] and new_promos and not first_run:
        relevant = [p for p in new_promos if p["discount"] is None or p["discount"] >= settings["minDiscount"]
                    or matches_keywords(p["title"], settings["keywords"])]
        if relevant:
            lines = [f"📣 <b>Promociones nuevas</b>"]
            for p in relevant[:12]:
                pct = f" (−{p['discount']} %)" if p["discount"] else ""
                lines.append(f"• {escape(STORES[p['store']]['name'])}: <a href=\"{escape(p['url'])}\">{escape(p['title'])}</a>{pct}")
            alerts.append("\n".join(lines))

    # 5) artículos en seguimiento
    alerts += check_tracked(fx, tracked, settings, d)

    # 6) guardar
    catalog = {"updated": now_iso(), "stores": store_status, "items": all_items}
    save(data_dir, "catalog.json", catalog, compact=True)
    save(data_dir, "prices.json", prices, compact=True)
    save(data_dir, "promos.json", promos)
    save(data_dir, "tracked.json", tracked)
    save(data_dir, "state.json", state)
    if not os.path.exists(os.path.join(data_dir, "settings.json")):
        save(data_dir, "settings.json", settings)

    print(f"Total: {len(all_items)} productos, {len(new_deals)} rebajas nuevas, {len(new_promos)} promos nuevas, primera vez={first_run}")
    if alerts and state.get("chatId"):
        telegram.send(state["chatId"], "\n\n".join(alerts) + f"\n\n<a href=\"{SITE_URL}\">Abrir Radar Airsoft</a>")


def check_tracked(fx, tracked, settings, d, only=None):
    alerts = []
    for t in tracked:
        if only and t["id"] != only:
            continue
        sid = t.get("store")
        html = fx.get(t["url"])
        if not html:
            host = urlsplit(t["url"]).netloc
            t["status"] = "error"
            t["note"] = ("La tienda no permite leer esta página de forma automática" if fx.blocked.get(host)
                         else f"No se pudo leer ({fx.errors.get(host) or 'error'})")
            continue
        info = extract_product(html, t["url"])
        if info["price"] is None:
            t["status"], t["note"] = "error", "No encontré el precio en la página"
            continue
        prev_price, prev_stock = t.get("price"), t.get("inStock")
        t.update({"price": info["price"], "regular": info["regular"], "inStock": info["inStock"],
                  "status": "ok", "note": None, "lastCheck": now_iso()})
        if not t.get("name"):
            t["name"] = info["name"]
        h = t.setdefault("history", [])
        if h and h[-1][0] == d:
            h[-1][1] = info["price"]
        else:
            h.append([d, info["price"]])
        del h[:-365]
        name = escape(t.get("name") or t["url"])
        store = escape(STORES.get(sid, {}).get("name", sid or ""))
        link = f"<a href=\"{escape(t['url'])}\">{name}</a>"
        if settings["notifyTarget"] and t.get("target") and info["price"] <= t["target"] and (prev_price is None or prev_price > t["target"] or info["price"] < prev_price):
            alerts.append(f"🎯 <b>Por debajo de tu objetivo</b>\n{store}: {link}\n{eur(info['price'])} (objetivo {eur(t['target'])})")
        elif settings["notifyDrop"] and prev_price and info["price"] < prev_price:
            alerts.append(f"📉 <b>Ha bajado de precio</b>\n{store}: {link}\n{eur(prev_price)} → <b>{eur(info['price'])}</b>")
        if settings["notifyStock"] and prev_stock is False and info["inStock"] is True:
            alerts.append(f"📦 <b>Vuelve a haber stock</b>\n{store}: {link} a {eur(info['price'])}")
    return alerts


# ---------------------------------------------------------------- bot

HELP = (
    "<b>Radar Airsoft</b> 🎯\n"
    "Te aviso de promociones, rebajas y cambios de precio.\n\n"
    "/seguir <i>enlace</i> [precio] — seguir un artículo (y avisarte si baja de ese precio)\n"
    "/lista — artículos que sigues\n"
    "/dejar <i>número</i> — dejar de seguir uno\n"
    "/buscar <i>palabras</i> — los más baratos del catálogo\n"
    "/ofertas — las mayores rebajas ahora\n"
    "/minimo <i>%</i> — descuento mínimo para avisarte\n"
    "/claves <i>a, b</i> — palabras que siempre te interesan (vacío para borrar)\n"
    "/avisos — ver tus ajustes\n"
    "/estado — estado de cada tienda\n\n"
    f"Web: {SITE_URL}"
)


def search_catalog(catalog, query, limit=8):
    toks = [t for t in norm(query).split() if len(t) > 1]
    syn = {"gbb": ["gbb", "gbbr", "blowback"], "gbbr": ["gbb", "gbbr", "blowback"], "aeg": ["aeg", "electric", "electrica"]}
    out = []
    for it in catalog.get("items", []):
        n = norm(it["n"])
        if all(any(s in n for s in syn.get(t, [t])) for t in toks):
            out.append(it)
    out.sort(key=lambda x: (x["k"] == 0, x["p"]))
    return out[:limit], len(out)


def run_bot(data_dir):
    state = load(data_dir, "state.json", {})
    tracked = load(data_dir, "tracked.json", [])
    settings = settings_of(data_dir)
    catalog = load(data_dir, "catalog.json", {"items": [], "stores": {}})
    offset = state.get("tgOffset", 0)
    ups = telegram.updates(offset)
    if not ups:
        print("Sin mensajes nuevos")
        return False
    changed = False
    fx = None
    for u in ups:
        state["tgOffset"] = u["update_id"] + 1
        changed = True
        msg = u.get("message") or u.get("edited_message")
        if not msg or "text" not in msg:
            continue
        chat = msg["chat"]["id"]
        allowed = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        if not state.get("chatId"):
            if allowed and str(chat) != allowed:
                continue
            state["chatId"] = chat
        if str(chat) != str(state["chatId"]):
            telegram.send(chat, "Este bot es privado.")
            continue
        text = msg["text"].strip()
        cmd, _, arg = text.partition(" ")
        cmd = cmd.split("@")[0].lower()
        arg = arg.strip()
        reply = None
        if cmd in ("/start", "/ayuda", "/help"):
            reply = HELP
        elif cmd == "/seguir":
            m = re.search(r"https?://\S+", arg)
            if not m:
                reply = "Pásame el enlace del producto: /seguir https://… 150"
            else:
                url = m.group(0).rstrip(").,")
                rest = arg.replace(m.group(0), "")
                pm = re.search(r"(\d+(?:[.,]\d{1,2})?)", rest)
                target = float(pm.group(1).replace(",", ".")) if pm else None
                if any(t["url"] == url for t in tracked):
                    reply = "Ese artículo ya está en tu lista."
                else:
                    sid = next((k for k, c in STORES.items() if urlsplit(c["home"]).netloc.replace("www.", "") in url), None)
                    item = {"id": short_id(url), "url": url, "name": None, "store": sid, "target": target,
                            "price": None, "regular": None, "inStock": None, "status": "pending", "note": None,
                            "lastCheck": None, "addedAt": now_iso(), "history": []}
                    tracked.append(item)
                    fx = fx or Fetcher()
                    check_tracked(fx, tracked, settings, today(), only=item["id"])
                    if item["status"] == "ok":
                        stock = "" if item["inStock"] is None else (" · en stock" if item["inStock"] else " · sin stock")
                        reply = (f"✅ Siguiendo <b>{escape(item['name'] or url)}</b>\nAhora: {eur(item['price'])}{stock}"
                                 + (f"\nTe aviso si baja de {eur(target)}" if target else "\nTe aviso si baja de precio"))
                    else:
                        reply = f"Añadido, pero no pude leer el precio: {escape(item['note'] or '')}"
        elif cmd == "/lista":
            if not tracked:
                reply = "No sigues ningún artículo. Usa /seguir enlace"
            else:
                lines = ["<b>Artículos que sigues</b>"]
                for i, t in enumerate(tracked, 1):
                    tgt = f" · objetivo {eur(t['target'])}" if t.get("target") else ""
                    lines.append(f"{i}. <a href=\"{escape(t['url'])}\">{escape(t.get('name') or t['url'])}</a> — {eur(t.get('price'))}{tgt}")
                reply = "\n".join(lines)
        elif cmd == "/dejar":
            try:
                i = int(arg) - 1
                t = tracked.pop(i)
                reply = f"Dejo de seguir {escape(t.get('name') or t['url'])}"
            except (ValueError, IndexError):
                reply = "Dime el número de /lista, por ejemplo: /dejar 2"
        elif cmd == "/minimo":
            try:
                settings["minDiscount"] = max(0, min(90, int(float(arg.replace("%", "")))))
                save(data_dir, "settings.json", settings)
                reply = f"Te avisaré de rebajas desde un {settings['minDiscount']} %."
            except ValueError:
                reply = "Ejemplo: /minimo 25"
        elif cmd == "/claves":
            settings["keywords"] = [k.strip().lower() for k in arg.split(",") if k.strip()]
            save(data_dir, "settings.json", settings)
            reply = ("Palabras clave: " + ", ".join(settings["keywords"])) if settings["keywords"] else "Palabras clave borradas."
        elif cmd == "/avisos":
            reply = (f"Descuento mínimo: {settings['minDiscount']} %\n"
                     f"Palabras clave: {', '.join(settings['keywords']) or '—'}\n"
                     f"Rebajas: {'sí' if settings['notifyDeals'] else 'no'} · Promos: {'sí' if settings['notifyPromos'] else 'no'}\n"
                     f"Bajadas en seguimiento: {'sí' if settings['notifyDrop'] else 'no'} · Objetivo: {'sí' if settings['notifyTarget'] else 'no'} · Stock: {'sí' if settings['notifyStock'] else 'no'}")
        elif cmd == "/buscar":
            if not arg:
                reply = "Ejemplo: /buscar noveske gbb n4"
            else:
                res, total = search_catalog(catalog, arg)
                if not res:
                    reply = f"No hay nada en el catálogo con «{escape(arg)}». Prueba en la web: {SITE_URL}"
                else:
                    lines = [f"<b>{total} resultado(s) para «{escape(arg)}»</b> (más baratos primero)"]
                    for it in res:
                        ss = " · sin stock" if it["k"] == 0 else ""
                        lines.append(f"• {eur(it['p'])} — {escape(STORES[it['s']]['name'])}: <a href=\"{escape(it['u'])}\">{escape(it['n'])}</a>{ss}")
                    reply = "\n".join(lines)
        elif cmd == "/ofertas":
            deals = [it for it in catalog.get("items", []) if it.get("r") and it["r"] > it["p"] and it["k"] != 0]
            deals.sort(key=lambda it: -(1 - it["p"] / it["r"]))
            lines = ["<b>Mayores rebajas ahora</b>"]
            for it in deals[:10]:
                pct = round(100 * (1 - it["p"] / it["r"]))
                lines.append(f"• −{pct} % {escape(STORES[it['s']]['name'])}: <a href=\"{escape(it['u'])}\">{escape(it['n'])}</a> {eur(it['p'])}")
            reply = "\n".join(lines) if deals else "Todavía no hay rebajas en el catálogo."
        elif cmd == "/estado":
            lines = [f"<b>Última lectura</b>: {catalog.get('updated', '—')}"]
            for sid, s in catalog.get("stores", {}).items():
                lines.append(f"• {escape(s.get('name', sid))}: {s.get('status')} · {s.get('count', 0)} productos")
            reply = "\n".join(lines)
        else:
            reply = "No entiendo ese comando.\n\n" + HELP
        telegram.send(chat, reply)
    save(data_dir, "state.json", state)
    save(data_dir, "tracked.json", tracked)
    return changed


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "catalog"
    data_dir = sys.argv[2] if len(sys.argv) > 2 else "data"
    t0 = time.time()
    if mode == "catalog":
        run_catalog(data_dir)
    elif mode == "bot":
        changed = run_bot(data_dir)
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            with open(out, "a") as f:
                f.write(f"changed={'true' if changed else 'false'}\n")
    else:
        sys.exit(f"Modo desconocido: {mode}")
    print(f"Hecho en {time.time() - t0:.0f} s")
