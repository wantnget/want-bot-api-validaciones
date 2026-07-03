import azure.functions as func

import documentos
app = func.FunctionApp()


@app.route(route="descargar_truora", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def descargar_truora_route(req: func.HttpRequest) -> func.HttpResponse:
    return documentos.main(req)

@app.route(route="validar_identidad", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def validar_identidad_route(req: func.HttpRequest) -> func.HttpResponse:
    return validar_identidad_route.main(req)