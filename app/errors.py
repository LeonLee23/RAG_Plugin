class AppError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable

    def payload(self, request_id: str = "") -> dict:
        return {"request_id": request_id, "error": {
            "code": self.code, "message": self.message, "retryable": self.retryable,
        }}
