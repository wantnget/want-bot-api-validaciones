import re
import io
import json
import base64
import unicodedata

import psycopg2
import pdfplumber
import azure.functions as func

from shared.auth import validar_api_key
from shared.exceptions import AuthError
from shared.logger import get_logger

log = get_logger("validar_identidad")

CAMPOS_REQUERIDOS = ("radicado", "nombre", "cedula")

PG_HOST = "pg-coopvalili.postgres.database.azure.com"
PG_PORT = "5432"
PG_DATABASE = "wants_db"
PG_USER = "pgadmin"
PG_PASSWORD = "Motores2026Want"


def _validar_campos(payload: dict) -> list:
    return [c for c in CAMPOS_REQUERIDOS if payload.get(c) is None]


def get_conn():
    return psycopg2.connect(
        host=PG_HOST, port=PG_PORT, dbname=PG_DATABASE,
        user=PG_USER, password=PG_PASSWORD, sslmode="require",
    )


# ---------- Normalización ----------

def quitar_tildes(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in texto if not unicodedata.combining(c))


def normalizar_nombre(nombre: str) -> str:
    if not nombre:
        return ""
    nombre = nombre.replace("\n", " ").upper().strip()
    nombre = quitar_tildes(nombre)
    return re.sub(r"\s+", " ", nombre)


def normalizar_numero(numero: str) -> str:
    if not numero:
        return ""
    return re.sub(r"\D", "", numero)


# ---------- Extracción de texto ----------

def extraer_texto_pdf(contenido_bytes: bytes) -> str:
    texto = ""
    with pdfplumber.open(io.BytesIO(contenido_bytes)) as pdf:
        for pagina in pdf.pages:
            texto += (pagina.extract_text() or "") + "\n"
    return texto


# ---------- Carta Laboral ----------

def extraer_datos_carta_laboral(texto: str):
    cedula_match = re.search(
        r"c[eé]dula de ciudadan[ií]a\s*No\.?\s*([\d\.]+)", texto, re.IGNORECASE
    )
    cedula = cedula_match.group(1) if cedula_match else None

    nombre_match = re.search(
        r"[Qq]ue\s+([A-ZÁÉÍÓÚÑ\s]+),\s*identificado", texto
    )
    nombre = nombre_match.group(1) if nombre_match else None

    return normalizar_nombre(nombre), normalizar_numero(cedula)


# ---------- Desprendible de Pago ----------

def extraer_datos_desprendible_tabla(pdf_bytes: bytes):
    """Extrae por tablas: une correctamente celdas con nombre en varias lineas."""
    nombre = None
    cedula = None
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for pagina in pdf.pages:
            for tabla in pagina.extract_tables():
                for fila in tabla:
                    celdas = [(c or "").replace("\n", " ").strip() for c in fila]
                    for i, celda in enumerate(celdas):
                        etiqueta = quitar_tildes(celda).lower()
                        if etiqueta == "empleado" and nombre is None and i + 1 < len(celdas):
                            nombre = celdas[i + 1]
                        if etiqueta == "cedula" and cedula is None and i + 1 < len(celdas):
                            cedula = celdas[i + 1]
    return normalizar_nombre(nombre), normalizar_numero(cedula)


def extraer_datos_desprendible_texto(texto: str):
    """Fallback por texto plano (PDFs sin lineas de tabla detectables)."""
    cedula_match = re.search(
        r"C[eé]dula\s*:?\s*([\d\.,]{6,})", texto, re.IGNORECASE
    )
    cedula = cedula_match.group(1) if cedula_match else None

    nombre_match = re.search(
        r"Empleado\s*:?\s*(.+?)\s+C[eé]dula", texto, re.IGNORECASE | re.DOTALL
    )
    nombre = nombre_match.group(1) if nombre_match else None

    return normalizar_nombre(nombre), normalizar_numero(cedula)


def extraer_datos_desprendible(pdf_bytes: bytes):
    """Primero intenta por tabla; si falta algo, complementa con texto plano."""
    nombre, cedula = extraer_datos_desprendible_tabla(pdf_bytes)

    if not nombre or not cedula:
        texto = extraer_texto_pdf(pdf_bytes)
        nombre_txt, cedula_txt = extraer_datos_desprendible_texto(texto)
        nombre = nombre or nombre_txt
        cedula = cedula or cedula_txt

    return nombre, cedula


# ---------- Obtener documentos por radicado ----------

def obtener_documentos_radicado(radicado: str) -> dict:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT response_json
                FROM document_results
                WHERE radicado = %s
                ORDER BY id ASC
                """,
                (radicado,),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        raise ValueError(f"No se encontraron documentos para radicado {radicado}")

    documentos = {}
    for (response_json,) in rows:
        data = response_json if isinstance(
            response_json, dict) else json.loads(response_json)
        info = data.get("info", {})
        nombre_archivo = info.get("nombre_archivo", "")
        b64 = info.get("base64")
        if not b64:
            continue
        contenido = base64.b64decode(b64)

        if nombre_archivo.startswith("carta_laboral_"):
            documentos["carta_laboral"] = contenido
        elif nombre_archivo.startswith("desprendible_de_pago_"):
            documentos["desprendible"] = contenido

    return documentos


# ---------- Handler ----------

def main(req: func.HttpRequest) -> func.HttpResponse:
    log.info("request recibido", extra={"endpoint": "/api/validar_identidad"})

    try:
        validar_api_key(req.headers.get("x-api-key"))
    except AuthError as exc:
        log.warning("auth fallida", extra={"reason": str(exc)})
        return func.HttpResponse(
            json.dumps({"error": str(exc), "code": exc.code}),
            status_code=401,
            mimetype="application/json",
        )

    try:
        payload = req.get_json()
    except ValueError:
        log.warning("body JSON invalido")
        return func.HttpResponse(
            json.dumps({"error": "Body JSON inválido"}),
            status_code=400,
            mimetype="application/json",
        )

    faltantes = _validar_campos(payload)
    if faltantes:
        log.warning("campos faltantes", extra={"campos": faltantes})
        return func.HttpResponse(
            json.dumps({"error": f"Campos requeridos faltantes: {faltantes}"}),
            status_code=400,
            mimetype="application/json",
        )

    radicado = str(payload.get("radicado"))
    nombre_truora = normalizar_nombre(str(payload.get("nombre")))
    cedula_truora = normalizar_numero(str(payload.get("cedula")))

    try:
        documentos = obtener_documentos_radicado(radicado)
    except ValueError as exc:
        log.warning("documentos no encontrados", extra={
                    "radicado": radicado, "error": str(exc)})
        return func.HttpResponse(
            json.dumps(
                {"status": "error", "radicado": radicado, "error": str(exc)}),
            status_code=404,
            mimetype="application/json",
        )
    except Exception as exc:
        log.warning("fallo consulta db", extra={
                    "radicado": radicado, "error": str(exc)})
        return func.HttpResponse(
            json.dumps({"status": "error", "radicado": radicado,
                        "error": f"Error consultando DB: {str(exc)[:200]}"}),
            status_code=502,
            mimetype="application/json",
        )

    faltan_docs = [d for d in ("carta_laboral", "desprendible")
                   if d not in documentos]
    if faltan_docs:
        log.warning("documentos incompletos", extra={
                    "radicado": radicado, "faltan": faltan_docs})
        return func.HttpResponse(
            json.dumps({"status": "error", "radicado": radicado,
                        "error": f"Faltan documentos: {faltan_docs}"}),
            status_code=404,
            mimetype="application/json",
        )

    try:
        texto_carta = extraer_texto_pdf(documentos["carta_laboral"])
        nombre_carta, cedula_carta = extraer_datos_carta_laboral(texto_carta)

        # el desprendible recibe BYTES (tabla + fallback texto)
        nombre_desp, cedula_desp = extraer_datos_desprendible(
            documentos["desprendible"])
    except Exception as exc:
        log.warning("fallo lectura pdf", extra={
                    "radicado": radicado, "error": str(exc)})
        return func.HttpResponse(
            json.dumps({"status": "error", "radicado": radicado,
                        "error": f"Error leyendo PDF: {str(exc)[:200]}"}),
            status_code=422,
            mimetype="application/json",
        )

    nombres_coinciden = (
        nombre_truora == nombre_carta == nombre_desp
        and nombre_truora != ""
    )
    cedulas_coinciden = (
        cedula_truora == cedula_carta == cedula_desp
        and cedula_truora != ""
    )

    resultado_validacion = 1 if (nombres_coinciden and cedulas_coinciden) else 2

    out = {
        "status": "ok",
        "radicado": radicado,
        "resultado_validacion": resultado_validacion,
        "detalle": {
            "nombre_truora": nombre_truora,
            "nombre_carta_laboral": nombre_carta,
            "nombre_desprendible": nombre_desp,
            "nombres_coinciden": nombres_coinciden,
            "cedula_truora": cedula_truora,
            "cedula_carta_laboral": cedula_carta,
            "cedula_desprendible": cedula_desp,
            "cedulas_coinciden": cedulas_coinciden,
        },
    }

    log.info("validacion completada", extra={
        "radicado": radicado,
        "resultado_validacion": resultado_validacion,
    })

    return func.HttpResponse(
        json.dumps(out, ensure_ascii=False),
        status_code=200,
        mimetype="application/json",
    )