import azure.functions as func

import documentos

app = func.FunctionApp()


@app.route(route="descargar_truora", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
def descargar_truora_route(req: func.HttpRequest) -> func.HttpResponse:
    return documentos.main(req)