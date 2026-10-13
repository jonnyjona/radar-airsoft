# Radar Airsoft

Comparador de precios y promociones de tiendas de airsoft, con avisos por Telegram.
Funciona solo, sin Claude: una web estática en GitHub Pages y un programa que se
ejecuta en GitHub Actions.

**Web:** https://jonnyjona.github.io/radar-airsoft/

## Qué hace

- Cada 2 horas lee el catálogo de réplicas y las promociones de Hobby Expert, Gunfire,
  Powair6, Evike Europe, Ops-Store y Taiwangun.
- Respeta el `robots.txt` de cada tienda: lo que una tienda no permite leer de forma
  automática se salta y aparece así en la pestaña **Tiendas y avisos**.
- La web busca al instante por palabras clave dentro de ese catálogo, enseña las
  campañas y las réplicas rebajadas, y guarda el historial de precios.
- Cada producto tiene una ficha con **precio en vivo** (lee la tienda en ese momento y
  puede repetirlo cada 5 minutos), historial y el mismo producto en otras tiendas.
- El bot de Telegram avisa de campañas y réplicas rebajadas nuevas, y de los artículos
  que sigues cuando bajan de X € o llegan a Y % de descuento.

## Cómo está montado

| Parte | Dónde |
|---|---|
| Lector de tiendas y bot | `scraper/` (Python) |
| Web | `site/index.html` |
| Ejecución | `radar.yml` (tiendas, cada 2 h), `bot.yml` (Telegram, cada 10 min), `live.yml` (precio en vivo), `web.yml` (cambios desde la web), `pages.yml` (publicar la web) |
| Ajustes y seguimiento | `config/` en la rama `main` |
| Datos | rama `data` (catálogo, precios, campañas; se reescribe en cada lectura) y rama `live` (lecturas en vivo) |

## Puesta en marcha

1. **Pages**: Settings → Pages → Source: **GitHub Actions**.
2. **Telegram**: crea un bot con @BotFather y guarda su token en
   Settings → Secrets and variables → Actions → New repository secret, con el nombre
   `TELEGRAM_TOKEN`. Luego envía `/start` a tu bot: el primer chat que escriba queda
   como dueño del bot.
3. Opcional: secret `TELEGRAM_CHAT_ID` con tu id de chat, para que nadie más pueda
   quedarse el bot.
4. Lanza la primera lectura en Actions → "Radar Airsoft (tiendas)" → Run workflow.

## Comandos del bot

`/seguir enlace [precio]`, `/lista`, `/dejar n`, `/buscar palabras`, `/ofertas`,
`/minimo %`, `/claves a, b`, `/avisos`, `/estado`.

## Añadir una tienda

Añade una entrada en `scraper/stores.py` con sus páginas de categoría, sus páginas de
promociones y la expresión que reconoce sus fichas de producto.
