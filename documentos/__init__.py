import os
import io
import json
import base64
import zipfile
import mimetypes
from datetime import datetime, timezone
from urllib.parse import urlparse, unquote, parse_qs

import requests
import azure.functions as func

from shared.auth import validar_api_key
from shared.exceptions import AuthError
from shared.logger import get_logger
from shared import db_client

log = get_logger("descargar_truora")

CAMPOS_REQUERIDOS = ("link", "radicado", "tipo_documento")


def _validar_campos(payload: dict) -> list:
    return [c for c in CAMPOS_REQUERIDOS if payload.get(c) is None]


def verificar_expiracion(url: str) -> dict:
    qs = parse_qs(urlparse(url).query)
    fecha = qs.get("X-Amz-Date", [None])[0]
    expires = qs.get("X-Amz-Expires", [None])[0]

    info = {"firmado": bool(fecha and expires)}
    if fecha and expires:
        emitido = datetime.strptime(
            fecha, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        vence_ts = emitido.timestamp() + int(expires)
        ahora = datetime.now(timezone.utc).timestamp()
        restante = vence_ts - ahora

        info["emitido"] = emitido.isoformat()
        info["expira"] = datetime.fromtimestamp(
            vence_ts, tz=timezone.utc).isoformat()
        info["segundos_restantes"] = int(restante)

        if restante <= 0:
            raise RuntimeError(
                f"El URL pre-firmado ya EXPIRO (vencio hace {int(-restante)} s). "
                "Genera un nuevo link desde Truora y vuelve a ejecutar."
            )
    return info


def detectar_extension_texto(contenido: bytes) -> str:
    """Detecta tipos de documento de texto (JSON, HTML, XML, CSV/plano)."""
    try:
        texto = contenido[:2048].decode("utf-8", errors="strict").strip()
    except UnicodeDecodeError:
        return ""

    if not texto:
        return ""

    if texto[0] in ("{", "["):
        try:
            json.loads(contenido.decode("utf-8"))
            return ".json"
        except Exception:
            pass

    texto_lower = texto.lower()
    if texto_lower.startswith("<!doctype html") or "<html" in texto_lower[:200]:
        return ".html"

    if texto.startswith("<?xml"):
        return ".xml"

    # Texto plano: si es imprimible y tiene saltos de línea / comas típicas de CSV
    imprimible = all(c.isprintable() or c in "\r\n\t" for c in texto)
    if imprimible:
        if texto.count(",") > 2 and "\n" in texto:
            return ".csv"
        return ".txt"

    return ""


def detectar_extension(contenido: bytes) -> str:
    if contenido[:4] == b"PK\x03\x04" or contenido[:4] == b"PK\x05\x06":
        try:
            with zipfile.ZipFile(io.BytesIO(contenido)) as z:
                nombres = z.namelist()
                if any(n.startswith("xl/") for n in nombres):
                    return ".xlsx"
                if any(n.startswith("word/") for n in nombres):
                    return ".docx"
                if any(n.startswith("ppt/") for n in nombres):
                    return ".pptx"
        except Exception:
            pass
        return ".zip"
    if contenido[:4] == b"%PDF":
        return ".pdf"
    if contenido[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if contenido[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if contenido[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if contenido[:4] == b"\xd0\xcf\x11\xe0":
        return ".xls"

    ext_texto = detectar_extension_texto(contenido)
    if ext_texto:
        return ext_texto

    return ""


def nombre_archivo(url: str, response: requests.Response, tipo_documento: str = "") -> str:
    contenido = response.content

    cd = response.headers.get("content-disposition", "")
    if "filename=" in cd:
        nombre = unquote(cd.split("filename=")[-1].strip('"; '))
    else:
        nombre = unquote(os.path.basename(
            urlparse(url).path)) or "documento_truora"

    ext_real = detectar_extension(contenido)
    nombre_sin_ext, ext_actual = os.path.splitext(nombre)
    if ext_real:
        if ext_actual.lower() != ext_real:
            nombre = nombre_sin_ext + ext_real
    elif not ext_actual:
        ext_ct = mimetypes.guess_extension(
            response.headers.get("content-type", "").split(";")[0]
        ) or ".bin"
        nombre = nombre + ext_ct

    if tipo_documento:
        tipo_limpio = tipo_documento.strip().strip("-_").replace(" ", "_")
        nombre_base, ext_final = os.path.splitext(nombre)
        nombre = f"{tipo_limpio}_{nombre_base}{ext_final}"

    return nombre


def main(req: func.HttpRequest) -> func.HttpResponse:
    log.info("request recibido", extra={"endpoint": "/api/descargar_truora"})

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

    link = str(payload.get("link"))
    radicado = str(payload.get("radicado"))
    tipo_documento = str(payload.get("tipo_documento"))

    try:
        meta_url = verificar_expiracion(link)
    except RuntimeError as exc:
        log.warning("url expirado", extra={
                    "radicado": radicado, "error": str(exc)})
        return func.HttpResponse(
            json.dumps(
                {"status": "error", "radicado": radicado, "error": str(exc)}),
            status_code=410,  # Gone
            mimetype="application/json",
        )

    try:
        response = requests.get(link, timeout=60)
    except requests.RequestException as exc:
        log.warning("fallo descarga", extra={
                    "radicado": radicado, "error": str(exc)})
        return func.HttpResponse(
            json.dumps({"status": "error", "radicado": radicado,
                        "error": f"No se pudo descargar: {str(exc)[:200]}"}),
            status_code=502,
            mimetype="application/json",
        )

    if response.status_code == 403:
        log.warning("s3 403", extra={
                    "radicado": radicado, "body": response.text[:200]})
        return func.HttpResponse(
            json.dumps({
                "status": "error",
                "radicado": radicado,
                "error": "S3 rechazo la peticion (403). URL expirado o firma invalida. "
                         "Vuelve a generar el link desde Truora.",
                "s3_response": response.text[:500],
            }),
            status_code=403,
            mimetype="application/json",
        )

    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        log.warning("http error descarga", extra={
                    "radicado": radicado, "status": response.status_code})
        return func.HttpResponse(
            json.dumps({"status": "error", "radicado": radicado,
                        "error": f"HTTP {response.status_code}: {str(exc)[:200]}"}),
            status_code=502,
            mimetype="application/json",
        )

    contenido = response.content
    nombre = nombre_archivo(link, response, tipo_documento)
    extension = os.path.splitext(nombre)[1]
    contenido_b64 = base64.b64encode(contenido).decode("utf-8")

    log.info("documento descargado", extra={
        "radicado": radicado,
        "tipo_documento": tipo_documento,
        "nombre": nombre,
        "tamano": len(contenido),
    })

    out = {
        "status": "ok",
        "message": "Documento descargado correctamente",
        "radicado": radicado,
        "tipo_documento": tipo_documento,
        "info": {
            "nombre_archivo": nombre,
            "extension": extension,
            "tamano_bytes": len(contenido),
            "base64": contenido_b64,
        },
    }
    cedula = radicado.split("_", 1)[0] if "_" in radicado else ""

    try:
        db_client.document_results(radicado, cedula, payload, out)
        out["db_status"] = "OK"
        log.info("document_results guardado", extra={
                 "radicado": radicado, "cedula": cedula})
    except Exception as exc:
        log.warning("fallo db document_results", extra={
                    "radicado": radicado, "error": str(exc)})
        out["db_status"] = f"ERROR: {str(exc)[:200]}"

    return func.HttpResponse(
        json.dumps(out, ensure_ascii=False, default=str),
        status_code=200,
        mimetype="application/json",
    )