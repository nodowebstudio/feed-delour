"""
Generador de feed de productos para Google Merchant Center y Meta Commerce Manager.

Lee el catalogo de delour.mx en JSON, obtiene la imagen de cada ficha,
y escribe dos archivos: google.xml y meta.csv

Uso:  python3 generar_feed.py
"""

import csv
import json
import time
import re
import html
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------
# CONFIGURACION  (esto es lo unico que normalmente vas a tocar)
# ---------------------------------------------------------------

SITIO = "https://delour.mx"
CATALOGO_JSON = f"{SITIO}/productos.json"

MARCA = "Delour"
MONEDA = "MXN"
IDIOMA = "es"
PAIS = "MX"

SALIDA = Path(__file__).parent / "publico"
CACHE = Path(__file__).parent / "cache_imagenes.json"

# Pausa entre peticiones al sitio, en segundos. No lo bajes de 0.5
PAUSA = 0.7

# categories_id del JSON  ->  categoria de Google
# Completa esta tabla conforme confirmes los IDs con la duena o el dev.
CATEGORIAS = {}
CATEGORIA_DEFAULT = "2899"

# Productos que NO deben salir al feed (ej. cursos, talleres, servicios)
EXCLUIR_IDS = set()

# ---------------------------------------------------------------
# PASO 1 - Traer el catalogo
# ---------------------------------------------------------------


def traer_catalogo():
    print(f"Leyendo catálogo desde {CATALOGO_JSON} ...")
    productos, vistos, pagina = [], set(), 1

    while pagina <= 50:
        r = requests.get(f"{CATALOGO_JSON}?page={pagina}", timeout=30)
        r.raise_for_status()
        lote = r.json()

        if isinstance(lote, dict):
            lote = lote.get("productos") or lote.get("products") or lote.get("data") or []
        if not lote:
            break

        nuevos = [p for p in lote if p["id"] not in vistos]
        if not nuevos:
            break

        vistos.update(p["id"] for p in nuevos)
        productos.extend(nuevos)
        print(f"  página {pagina}: {len(nuevos)} productos")

        pagina += 1
        time.sleep(PAUSA)

    print(f"  -> {len(productos)} productos en total")
    return productos

# ---------------------------------------------------------------
# PASO 2 - Sacar la imagen de cada ficha (con cache)
# ---------------------------------------------------------------


def cargar_cache():
    if CACHE.exists():
        return json.loads(CACHE.read_text(encoding="utf-8"))
    return {}


def guardar_cache(cache):
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def obtener_imagen(slug, updated_at, cache):
    """Devuelve la URL de la imagen principal leyendo el og:image de la ficha."""
    clave = f"{slug}|{updated_at}"
    if clave in cache:
        return cache[clave]

    url = f"{SITIO}/productos/{slug}"
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        sopa = BeautifulSoup(r.text, "html.parser")
        etiqueta = sopa.find("meta", property="og:image")
        imagen = etiqueta["content"] if etiqueta and etiqueta.get("content") else None
    except Exception as e:
        print(f"  !! No se pudo leer la imagen de {slug}: {e}")
        imagen = None

    cache[clave] = imagen
    time.sleep(PAUSA)
    return imagen


# ---------------------------------------------------------------
# PASO 3 - Normalizar un producto del JSON a los campos del feed
# ---------------------------------------------------------------


def limpiar_texto(txt, limite):
    if not txt:
        return ""
    txt = str(txt)
    txt = re.sub(r"<\s*br\s*/?\s*>", " ", txt, flags=re.I)
    txt = re.sub(r"</\s*(p|div|li|h[1-6])\s*>", " ", txt, flags=re.I)
    txt = re.sub(r"<[^>]+>", "", txt)
    txt = html.unescape(txt)
    txt = re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", "", txt)
    txt = " ".join(txt.split())
    return txt[:limite]

def normalizar(p, imagen):
    precio = p.get("price_offer") or p.get("price")
    try:
        precio = f"{float(precio):.2f}"
    except (TypeError, ValueError):
        return None

    titulo = limpiar_texto(p.get("title_seo") or p.get("name"), 150)
    descripcion = limpiar_texto(p.get("meta_description") or p.get("description"), 5000)

    return {
        "id": str(p["id"]),
        "title": titulo,
        "description": descripcion,
        "link": f"{SITIO}/productos/{quote(p['slug'])}",
        "image_link": imagen,
        "availability": "in_stock",
        "condition": "new",
        "price": f"{precio} {MONEDA}",
        "brand": MARCA,
        "identifier_exists": "no",
        "google_product_category": CATEGORIAS.get(
            p.get("categories_id"), CATEGORIA_DEFAULT
        ),
    }


# ---------------------------------------------------------------
# PASO 4 - Escribir el XML para Google
# ---------------------------------------------------------------

NS_G = "http://base.google.com/ns/1.0"


def escribir_xml(items, destino):
    ET.register_namespace("g", NS_G)
    rss = ET.Element("rss", {"version": "2.0"})
    canal = ET.SubElement(rss, "channel")
    ET.SubElement(canal, "title").text = "Delour Floristería de Autor"
    ET.SubElement(canal, "link").text = SITIO
    ET.SubElement(canal, "description").text = (
        "Catálogo de arreglos florales y bouquets con entrega en Mérida, Yucatán."
    )

    for it in items:
        nodo = ET.SubElement(canal, "item")
        for campo, valor in it.items():
            ET.SubElement(nodo, f"{{{NS_G}}}{campo}").text = valor

    arbol = ET.ElementTree(rss)
    ET.indent(arbol, space="  ")
    arbol.write(destino, encoding="utf-8", xml_declaration=True)


# ---------------------------------------------------------------
# PASO 5 - Escribir el CSV para Meta
# ---------------------------------------------------------------

COLUMNAS_META = [
    "id",
    "title",
    "description",
    "availability",
    "condition",
    "price",
    "link",
    "image_link",
    "brand",
    "google_product_category",
]


def escribir_csv(items, destino):
    with open(destino, "w", newline="", encoding="utf-8") as f:
        escritor = csv.DictWriter(f, fieldnames=COLUMNAS_META, extrasaction="ignore")
        escritor.writeheader()
        for it in items:
            escritor.writerow(it)


# ---------------------------------------------------------------
# ORQUESTADOR
# ---------------------------------------------------------------


def main():
    SALIDA.mkdir(exist_ok=True)
    cache = cargar_cache()
    productos = traer_catalogo()

    items = []
    descartados = []

    for p in productos:
        if p["id"] in EXCLUIR_IDS:
            continue

        imagen = obtener_imagen(p["slug"], p.get("updated_at", ""), cache)
        item = normalizar(p, imagen)

        if item is None:
            descartados.append((p["id"], p.get("name"), "precio inválido"))
            continue
        if not item["image_link"]:
            descartados.append((p["id"], p.get("name"), "sin imagen"))
            continue

        items.append(item)

    guardar_cache(cache)

    escribir_xml(items, SALIDA / "google.xml")
    escribir_csv(items, SALIDA / "meta.csv")

    sello = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n[{sello}]")
    print(f"  {len(items)} productos escritos")
    print(f"  -> {SALIDA / 'google.xml'}")
    print(f"  -> {SALIDA / 'meta.csv'}")

    if descartados:
        print(f"\n  {len(descartados)} descartados:")
        for pid, nombre, motivo in descartados:
            print(f"    #{pid} {nombre} — {motivo}")


if __name__ == "__main__":
    main()
