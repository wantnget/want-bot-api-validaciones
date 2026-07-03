import json
import os
from typing import Any, Dict
from azure.storage.blob import BlobServiceClient, ContentSettings


def _container():
    conn = os.environ.get("BLOB_CONN_STRING")
    if not conn:
        raise RuntimeError("BLOB_CONN_STRING no configurada")
    name = os.environ.get("BLOB_CONTAINER", "wants")
    return BlobServiceClient.from_connection_string(conn).get_container_client(name)


def save_json(blob_path: str, data: Dict[str, Any]) -> str:
    if not blob_path:
        raise RuntimeError("blob_path vacío")
    payload = json.dumps(data, ensure_ascii=False,
                         indent=2, default=str).encode("utf-8")
    _container().get_blob_client(blob_path).upload_blob(
        payload,
        overwrite=True,
        content_settings=ContentSettings(content_type="application/json"),
    )
    print(f"[blob] guardado → {blob_path}", flush=True)
    return blob_path


def save_validacion_req(radicado: str, data: Dict[str, Any]) -> str:
    return save_json(f"{radicado}/validacion_req.json", data)


def save_validacion_res(radicado: str, data: Dict[str, Any]) -> str:
    return save_json(f"{radicado}/validacion_res.json", data)
