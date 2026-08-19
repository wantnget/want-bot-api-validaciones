import json
import re
import unicodedata

import azure.functions as func
from shared.auth import validar_api_key
from shared.exceptions import AuthError
from shared.logger import get_logger

log = get_logger("obtener_codigo")

CAMPOS_REQUERIDOS = ("opcion",)

TEXTOS_OPCION = {
    1: "2 boletas 2D por $29.600 en 4 cuotas de $7.400",
    2: "4 boletas 2D por $59.200 en 4 cuotas de $14.800",
    3: "6 boletas 2D por $88.800 en 4 cuotas de $22.200",
    4: "2 boletas 2D más 2 combos por $60.800 en 4 cuotas de $15.200",
    5: "4 boletas 2D más 4 combos por $121.600 en 4 cuotas de $30.400",
    6: "6 boletas 2D más 6 combos por $182.400 en 4 cuotas de $45.600",
    7: "2 boletas 2D más 2 Super combos por $84.200 en 4 cuotas de $21.050",
}


def _validar_campos(payload: dict) -> list:
    return [c for c in CAMPOS_REQUERIDOS if payload.get(c) is None]


def _normalizar(texto: str) -> str:
    if not texto:
        return ""
    t = str(texto).strip().lower()
    t = "".join(
        ch for ch in unicodedata.normalize("NFD", t) if unicodedata.category(ch) != "Mn"
    )
    t = re.sub(r"\s+", " ", t)
    return t


def _extraer_numero_opcion(texto: str) -> int:
    match = re.search(r"\d+", texto)
    if not match:
        return 0
    return int(match.group())


def main(req: func.HttpRequest) -> func.HttpResponse:
    log.info("request recibido", extra={"endpoint": "/api/obtener_codigo"})

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

    opcion_raw = str(payload.get("opcion"))

    numero_opcion = _extraer_numero_opcion(_normalizar(opcion_raw))
    texto_opcion = TEXTOS_OPCION.get(numero_opcion, "")

    log.info(
        "opcion resuelta",
        extra={"opcion_raw": opcion_raw, "codigo": numero_opcion},
    )

    out = {"codigo": numero_opcion, "texto": texto_opcion}

    if not texto_opcion:
        out["status"] = "not_found"
        out["message"] = f"Opcion '{opcion_raw}' no reconocida"

    return func.HttpResponse(
        json.dumps(out, ensure_ascii=False),
        status_code=200,
        mimetype="application/json",
    )
