"""Configuración de las tiendas que lee Radar Airsoft.

Cada tienda define:
  - categories: páginas de listado de réplicas (el catálogo para el buscador).
  - promo_pages: páginas donde la tienda anuncia campañas y rebajas.
  - product_re: expresión que reconoce la URL de una ficha de producto.
  - page_param: cómo se pide la página N de un listado si no hay enlace "siguiente".
  - discover: prefijo de ruta para descubrir subcategorías desde las categorías semilla.

Todo se lee respetando robots.txt: si la tienda no permite leer una página
a este programa, se salta y queda anotado en el estado de la tienda.
"""

STORES = {
    "hobbyexpert": {
        "name": "Hobby Expert",
        "home": "https://www.hobbyexpert.es/",
        "categories": [
            "https://www.hobbyexpert.es/replicas-de-airsoft",
            "https://www.hobbyexpert.es/hpa-airsoft/unidades-hpa",
        ],
        "promo_pages": [
            "https://www.hobbyexpert.es/",
            "https://www.hobbyexpert.es/promociones/ofertas",
        ],
        "product_re": r"^https://www\.hobbyexpert\.es/(?!promociones|marcas|replicas-de-airsoft|equipamiento|accesorios|consumibles|internos|outdoor|aire-comprimido|cuchilleria|hpa-airsoft|customer|checkout|wishlist|blog|contact)[a-z0-9-]+$",
        "page_param": "p",
        "page_start": 1,
        "max_pages": 70,
        "delay": 12,
    },
    "gunfire": {
        "name": "Gunfire",
        "home": "https://gunfire.com/es/",
        "categories": [
            "https://gunfire.com/es/menu/armas-de-airsoft-366.html",
        ],
        "promo_pages": [
            "https://gunfire.com/es/about/promociones-72.html",
            "https://gunfire.com/en/menu/hot-deals-18201.html",
        ],
        "product_re": r"^https://gunfire\.com/(es|en)/products/[a-z0-9-]+-\d+\.html$",
        "page_param": "counter",
        "page_start": 0,
        "max_pages": 30,
        "note": "Precios con envío a Polonia (IVA 23 %); en España suelen salir algo más bajos.",
    },
    "powair6": {
        "name": "Powair6",
        "home": "https://www.powair6.com/en/",
        "categories": [
            "https://www.powair6.com/en/835-airsoft-gun",
            "https://www.powair6.com/en/873-hpa-replica",
        ],
        "promo_pages": [
            "https://www.powair6.com/en/",
        ],
        "product_re": r"^https://(www\.)?powair6\.com/en/[a-z0-9-]+/\d+-[a-z0-9-]+\.html$",
        "page_param": "page",
        "page_start": 1,
        "max_pages": 60,
        "prestashop_ajax": True,
        "note": "Precios con IVA francés.",
    },
    "evike": {
        "name": "Evike Europe",
        "home": "https://evike-europe.com/",
        "categories": [
            "https://evike-europe.com/airsoft-guns.html",
        ],
        "promo_pages": [
            "https://evike-europe.com/",
        ],
        "product_re": r"^https://evike-europe\.com/(?!airsoft-guns|accessories|platforms|exclusive-offers|gear|parts|customer|checkout|catalogsearch)[a-z0-9_-]+\.html$",
        "page_param": "p",
        "page_start": 1,
        "max_pages": 250,
    },
    "opsstore": {
        "name": "Ops-Store",
        "home": "https://www.ops-store.com/en/",
        "categories": [
            "https://www.ops-store.com/en/all-our-airsoft-guns/gbbr-amp-hpa-c1312.html",
        ],
        "discover": "https://www.ops-store.com/en/all-our-airsoft-guns/",
        "promo_pages": [
            "https://www.ops-store.com/en/promotions-m62045.html",
            "https://www.ops-store.com/en/airsoft-promotions-and-clearance-sales-c1291.html",
        ],
        "product_re": r"^https://www\.ops-store\.com/en/[a-z0-9-]*-a\d+\.html$",
        "page_param": "numPage",
        "page_start": 1,
        "max_pages": 30,
    },
    "taiwangun": {
        "name": "Taiwangun",
        "home": "https://www.taiwangun.com/",
        "categories": [
            "https://www.taiwangun.com/airsoft-guns",
        ],
        "promo_pages": [
            "https://www.taiwangun.com/",
        ],
        "product_re": r"^https://www\.taiwangun\.com/[a-z0-9-]+/[a-z0-9-]+$",
        "page_path": "/page:{n}",
        "page_start": 1,
        "max_pages": 80,
    },
}
