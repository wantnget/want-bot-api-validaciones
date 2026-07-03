class AuthError(Exception):
    def __init__(self, message: str = "No autorizado", code: str = "AUTH_ERROR"):
        super().__init__(message)
        self.code = code
