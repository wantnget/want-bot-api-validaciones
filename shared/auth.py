import hmac
import os

from .exceptions import AuthError
from .logger import get_logger

_log = get_logger("shared.auth")


def validar_api_key(request_key: str | None) -> None:

    expected = os.environ.get("API_KEY", "")

    if not expected:
        _log.warning("API_KEY no configurada — omitiendo validación de auth")
        return

    if not request_key:
        _log.warning("Solicitud sin x-api-key",
                     extra={"auth_result": "missing"})
        raise AuthError("Se requiere el header x-api-key")

    valid = hmac.compare_digest(
        request_key.encode("utf-8"),
        expected.encode("utf-8"),
    )

    if not valid:
        _log.warning("x-api-key inválida", extra={"auth_result": "invalid"})
        raise AuthError("API key inválida")

    _log.debug("Auth OK", extra={"auth_result": "ok"})
