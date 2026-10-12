"""Extracción de productos, precios y promociones desde el HTML de las tiendas.

Se intenta primero con datos estructurados (JSON-LD, atributos de precio de
Magento/PrestaShop) y, si no hay, con un lector genérico de "tarjetas" de producto.
"""
import hashlib
import json
import re
import unicodedata
from urllib.parse import urljoin, urldefrag

from bs4 import BeautifulSoup

PRICE_RE = re.compile(
    r"(?:€|EUR)\s*(\d{1,3}(?:[.\s  ]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)"
    r"|(\d{1,3}(?:[.\s  ]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:€|EUR)",
    re.I,
)
PCT_RE = re.compile(r"(\d{1,2})\s*%")
OUT_RE = re.compile(r"agotad|sin stock|fuera de stock|no disponible|out of stock|sold out|not available|unavailable|épuisé|rupture|niedostępn|brak", re.I)
IN_RE = re.compile(r"en stock|in stock|disponible|available|dostępn|ready to ship|listo para enviar", re.I)
SALE_RE = re.compile(
    r"%|rebaj|oferta|promo|descuent|\bsale|sales\b|deal|black ?(friday|week)|cyber|outlet|liquidac|aniversario|"
    r"soldes|destock|r[ée]duction|gratis|free shipping|cup[oó]n|c[oó]digo|code|hot|markdown|clearance|bons? plans?",
    re.I,
)


def norm(s):
    s = unicodedata.normalize("NFD", str(s or "").lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9.]+", " ", s).strip()


def short_id(*parts):
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:12]


def to_number(raw):
    raw = raw.replace(" ", " ").replace(" ", " ").strip()
    raw = raw.replace(" ", "")
    if "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        head, _, tail = raw.rpartition(",")
        raw = (head.replace(",", "") + "." + tail) if len(tail) in (1, 2) else raw.replace(",", "")
    elif "." in raw:
        head, _, tail = raw.rpartition(".")
        if len(tail) == 3 and head:
            raw = raw.replace(".", "")
    try:
        v = float(raw)
    except ValueError:
        return None
    return v if 0.5 <= v <= 20000 else None


def prices_in(text):
    out = []
    for m in PRICE_RE.finditer(text or ""):
        v = to_number(m.group(1) or m.group(2))
        if v is not None:
            out.append(v)
    return out


def stock_of(text):
    if OUT_RE.search(text or ""):
        return False
    if IN_RE.search(text or ""):
        return True
    return None


def clean(s):
    return re.sub(r"\s+", " ", s or "").strip()


def soup_of(html):
    return BeautifulSoup(html, "lxml")


# ---------- JSON-LD ----------

def _jsonld_blocks(soup):
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or tag.get_text() or "")
        except (ValueError, TypeError):
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            d = stack.pop()
            if isinstance(d, list):
                stack.extend(d)
            elif isinstance(d, dict):
                yield d
                if "@graph" in d:
                    stack.extend(d["@graph"] if isinstance(d["@graph"], list) else [d["@graph"]])
                if "itemListElement" in d:
                    stack.extend(d["itemListElement"] if isinstance(d["itemListElement"], list) else [d["itemListElement"]])
                if "item" in d and isinstance(d["item"], (dict, list)):
                    stack.append(d["item"])


def _is_type(d, name):
    t = d.get("@type")
    return name in t if isinstance(t, list) else t == name


def _offer_info(offers):
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    if not isinstance(offers, dict):
        return None, None
    price = offers.get("price") or offers.get("lowPrice")
    try:
        price = float(str(price).replace(",", ".")) if price not in (None, "") else None
    except ValueError:
        price = None
    avail = str(offers.get("availability") or "")
    stock = None
    if "InStock" in avail or "LimitedAvailability" in avail or "PreOrder" in avail:
        stock = True
    elif "OutOfStock" in avail or "SoldOut" in avail or "Discontinued" in avail:
        stock = False
    return price, stock


def product_from_jsonld(soup, url):
    for d in _jsonld_blocks(soup):
        if _is_type(d, "Product"):
            price, stock = _offer_info(d.get("offers"))
            if price:
                return {"name": clean(d.get("name")), "price": price, "inStock": stock, "url": url}
    return None


def listing_from_jsonld(soup, base):
    items = []
    for d in _jsonld_blocks(soup):
        if _is_type(d, "Product") and d.get("url"):
            price, stock = _offer_info(d.get("offers"))
            if price:
                items.append({"name": clean(d.get("name")), "url": urljoin(base, d["url"]), "price": price,
                              "regular": None, "inStock": stock})
    return items


# ---------- Magento ----------

def listing_magento(soup, base):
    items = []
    for card in soup.select("li.product-item, div.product-item-info"):
        a = card.select_one("a.product-item-link")
        if not a or not a.get("href"):
            continue
        final = card.select_one('[data-price-type="finalPrice"]')
        old = card.select_one('[data-price-type="oldPrice"]')
        try:
            price = float(final["data-price-amount"]) if final and final.get("data-price-amount") else None
        except ValueError:
            price = None
        if price is None:
            ps = prices_in(card.get_text(" "))
            price = min(ps) if ps else None
        try:
            regular = float(old["data-price-amount"]) if old and old.get("data-price-amount") else None
        except ValueError:
            regular = None
        if not price:
            continue
        txt = card.get_text(" ")
        items.append({"name": clean(a.get_text()), "url": urljoin(base, a["href"]), "price": price,
                      "regular": regular if regular and regular > price else None,
                      "inStock": False if card.select_one(".stock.unavailable, .out-of-stock") else stock_of(txt)})
    # una tarjeta puede aparecer dos veces (li + div): quitar duplicados
    seen, out = set(), []
    for it in items:
        if it["url"] not in seen:
            seen.add(it["url"])
            out.append(it)
    return out


# ---------- PrestaShop ----------

def listing_prestashop(soup, base):
    items = []
    for card in soup.select("article.product-miniature, div.product-miniature"):
        a = card.select_one(".product-title a, h2 a, h3 a, a.product-thumbnail")
        if not a or not a.get("href"):
            continue
        price_el = card.select_one("span.price, .product-price")
        price = None
        if price_el is not None:
            price = to_number(price_el.get("content")) if price_el.get("content") else None
            if price is None:
                ps = prices_in(price_el.get_text(" "))
                price = ps[0] if ps else None
        old_el = card.select_one(".regular-price")
        regular = None
        if old_el is not None:
            ps = prices_in(old_el.get_text(" "))
            regular = ps[0] if ps else None
        if not price:
            continue
        name = clean(a.get_text()) or clean(a.get("title"))
        if not name:
            img = card.select_one("img[alt]")
            name = clean(img["alt"]) if img else ""
        items.append({"name": name, "url": urljoin(base, a["href"]), "price": price,
                      "regular": regular if regular and regular > price else None,
                      "inStock": stock_of(card.get_text(" "))})
    return items


def listing_prestashop_json(data, base):
    """Respuesta JSON de PrestaShop 1.7 (?from-xhr / Accept: application/json)."""
    items = []
    for p in data.get("products") or []:
        url = p.get("url") or p.get("link")
        price = p.get("price_amount")
        if not url or not price:
            continue
        regular = p.get("regular_price_amount")
        qty = p.get("quantity")
        items.append({"name": clean(p.get("name")), "url": urljoin(base, url), "price": float(price),
                      "regular": float(regular) if regular and float(regular) > float(price) else None,
                      "inStock": (qty > 0) if isinstance(qty, (int, float)) else None})
    return items


# ---------- Genérico ----------

def listing_generic(soup, base, product_re):
    rx = re.compile(product_re)
    links = {}
    for a in soup.find_all("a", href=True):
        href = urldefrag(urljoin(base, a["href"]))[0].split("?")[0]
        if rx.match(href):
            links.setdefault(href, []).append(a)
    items = []
    for href, anchors in links.items():
        name = ""
        for a in anchors:
            name = clean(a.get_text()) or clean(a.get("title"))
            if not name:
                img = a.find("img", alt=True)
                name = clean(img["alt"]) if img else ""
            if len(name) > 4:
                break
        if len(name) < 5:
            continue
        # subir hasta la tarjeta: el ancestro con precio que no contenga otros productos
        node, card = anchors[0], None
        for _ in range(7):
            node = node.parent
            if node is None:
                break
            others = {urldefrag(urljoin(base, x["href"]))[0].split("?")[0] for x in node.find_all("a", href=True)}
            others = {o for o in others if rx.match(o)}
            if len(others) > 1:
                break
            if prices_in(node.get_text(" ")):
                card = node
                break
        if card is None:
            continue
        txt = card.get_text(" ")
        ps = prices_in(txt)
        first = ps[:2]
        price = min(first)
        regular = max(first) if len(first) == 2 and max(first) > price * 1.01 else None
        items.append({"name": name, "url": href, "price": price, "regular": regular, "inStock": stock_of(txt)})
    return items


def extract_listing(html, base, cfg):
    soup = soup_of(html)
    for fn in (listing_magento, listing_prestashop):
        items = fn(soup, base)
        if items:
            return items, soup, fn.__name__
    items = listing_from_jsonld(soup, base)
    if items:
        return items, soup, "jsonld"
    return listing_generic(soup, base, cfg["product_re"]), soup, "generic"


def next_page_url(soup, base):
    el = soup.select_one('link[rel="next"], a[rel="next"], a.next, a.action.next, li.pages-item-next a, a[aria-label*="Next"], a[aria-label*="Siguiente"], a[aria-label*="Suivant"]')
    if el and el.get("href"):
        return urljoin(base, el["href"])
    return None


# ---------- Ficha de producto ----------

def extract_product(html, url):
    soup = soup_of(html)
    found = product_from_jsonld(soup, url)
    name = found["name"] if found else ""
    if not name:
        og = soup.select_one('meta[property="og:title"]')
        h1 = soup.find("h1")
        name = clean(og["content"]) if og and og.get("content") else clean(h1.get_text() if h1 else "")
    price = found["price"] if found else None
    stock = found["inStock"] if found else None
    if price is None:
        for sel in ('meta[property="product:price:amount"]', 'meta[property="og:price:amount"]', '[itemprop="price"]'):
            el = soup.select_one(sel)
            if el is not None:
                price = to_number(el.get("content") or el.get_text())
                if price:
                    break
    if price is None:
        el = soup.select_one('[data-price-type="finalPrice"]')
        if el is not None and el.get("data-price-amount"):
            try:
                price = float(el["data-price-amount"])
            except ValueError:
                pass
    regular = None
    el = soup.select_one('[data-price-type="oldPrice"]')
    if el is not None and el.get("data-price-amount"):
        try:
            regular = float(el["data-price-amount"])
        except ValueError:
            pass
    if regular is None:
        for sel in (".regular-price", ".old-price", "del", "s", ".price-old", "#projector_price_maxprice"):
            el = soup.select_one(sel)
            if el is not None:
                ps = prices_in(el.get_text(" "))
                if ps:
                    regular = ps[0]
                    break
    if stock is None:
        main = soup.select_one("main") or soup
        stock = stock_of(main.get_text(" ")[:6000])
    if price is not None and regular is not None and regular <= price:
        regular = None
    return {"name": name, "price": price, "regular": regular, "inStock": stock}


# ---------- Promociones ----------

NAV_WORDS = {"ofertas", "outlet", "promociones", "promotions", "sale", "sales", "rebajas", "novedades", "deals", "hot deals"}


def extract_promos(html, base, store_id):
    soup = soup_of(html)
    for t in soup.select("nav, header nav, footer, .navigation, .nav, .menu, #menu, .breadcrumbs, .breadcrumb"):
        t.decompose()
    found = {}

    def add(text, href):
        text = clean(text)
        if not (8 <= len(text) <= 160) or not SALE_RE.search(text):
            return
        if norm(text) in NAV_WORDS or len(text.split()) < 3:
            return
        key = norm(text)
        if key in found:
            return
        pct = [int(x) for x in PCT_RE.findall(text) if 0 < int(x) < 95]
        found[key] = {
            "id": store_id + "-" + short_id(key),
            "store": store_id,
            "title": text,
            "url": urljoin(base, href) if href else base,
            "discount": max(pct) if pct else None,
        }

    for a in soup.find_all("a", href=True):
        img = a.find("img")
        if img is not None:
            add(img.get("alt") or img.get("title") or "", a["href"])
        if a.get("title"):
            add(a["title"], a["href"])
        add(a.get_text(" "), a["href"])
    for h in soup.find_all(["h1", "h2", "h3", "h4", "strong"]):
        a = h.find_parent("a") or h.find("a")
        add(h.get_text(" "), a["href"] if a and a.get("href") else None)
    return list(found.values())
