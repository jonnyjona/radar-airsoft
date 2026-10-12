# Radar Airsoft

Comparador de precios y promociones de tiendas de airsoft, con avisos por Telegram.
Funciona solo, sin Claude: una web estática en GitHub Pages y un programa que se
ejecuta en GitHub Actions.

**Web:** https://jonnyjona.github.io/radar-airsoft/

## Qué hace

- Cada 6 horas lee el catálogo de réplicas y las promociones de Hobby Expert, Gunfire,
  Powair6, Evike Europe, Ops-Store y Taiwangun.
- Respeta el `robots.txt` de cada tienda: lo que una tienda no permite leer de forma
  automática se salta y aparece así en la pestaña **Tiendas y avisos**.
- La web busca al instante por palabras clave dentro de ese catálogo, enseña las
  campañas y las réplicas rebajadas, y guarda el historial de precios.
- El bot de Telegram avisa de rebajas y campañas nuevas, y de bajadas de precio, precio
  objetivo o vuelta de stock en los artículos que sigues.

## Cómo está montado

| Parte | Dónde |
|---|---|
| Lector de tiendas y bot | `scraper/` (Python) |
| Web | `site/index.html` |
| Ejecución programada | `.github/workflows/radar.yml` (tiendas, cada 6 h) y `bot.yml` (mensajes, cada 10 min) |
| Datos | rama `data` (se reescribe en cada ejecución para que el repositorio no crezca) |

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
