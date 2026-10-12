"""Descargas educadas: respeta robots.txt, espera entre peticiones y se identifica."""
import time
import urllib.robotparser
from urllib.parse import urlsplit

import requests

BOT_NAME = "RadarAirsoftBot"
USER_AGENT = (
    "Mozilla/5.0 (compatible; RadarAirsoftBot/1.0; "
    "+https://github.com/jonnyjona/radar-airsoft)"
)
DEFAULT_DELAY = 1.5  # segundos entre peticiones a la misma tienda


class Fetcher:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.7",
        })
        self._robots = {}
        self._last = {}
        self.blocked = {}   # host -> nº de URLs saltadas por robots.txt
        self.errors = {}    # host -> último error

    def _robot(self, url):
        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        if base not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = self.session.get(base + "/robots.txt", timeout=20)
                if r.status_code >= 400:
                    rp.parse([])  # sin robots.txt: todo permitido
                else:
                    rp.parse(r.text.splitlines())
            except requests.RequestException:
                rp.parse([])
            self._robots[base] = rp
        return self._robots[base]

    def allowed(self, url):
        return self._robot(url).can_fetch(BOT_NAME, url)

    def _wait(self, host, url):
        delay = DEFAULT_DELAY
        try:
            cd = self._robot(url).crawl_delay(BOT_NAME)
            if cd:
                delay = max(delay, float(cd))
        except Exception:
            pass
        last = self._last.get(host)
        if last:
            pause = delay - (time.time() - last)
            if pause > 0:
                time.sleep(pause)
        self._last[host] = time.time()

    def get(self, url, headers=None, timeout=30):
        """Devuelve el texto de la página o None (bloqueada por robots o error)."""
        host = urlsplit(url).netloc
        if not self.allowed(url):
            self.blocked[host] = self.blocked.get(host, 0) + 1
            return None
        self._wait(host, url)
        try:
            r = self.session.get(url, headers=headers or {}, timeout=timeout)
        except requests.RequestException as e:
            self.errors[host] = f"{type(e).__name__}"
            return None
        if r.status_code != 200:
            self.errors[host] = f"HTTP {r.status_code}"
            return None
        r.encoding = r.encoding or "utf-8"
        return r.text
