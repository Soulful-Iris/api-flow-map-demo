"""HTTP status lookups across the naming styles of each framework."""
from __future__ import annotations

import re
from typing import Optional

REASONS = {
    200: "OK", 201: "Created", 202: "Accepted", 204: "No Content", 206: "Partial Content",
    301: "Moved Permanently", 302: "Found", 303: "See Other", 304: "Not Modified", 307: "Temporary Redirect", 308: "Permanent Redirect",
    400: "Bad Request", 401: "Unauthorized", 402: "Payment Required", 403: "Forbidden", 404: "Not Found",
    405: "Method Not Allowed", 406: "Not Acceptable", 408: "Request Timeout", 409: "Conflict", 410: "Gone",
    412: "Precondition Failed", 413: "Payload Too Large", 415: "Unsupported Media Type", 422: "Unprocessable Entity",
    423: "Locked", 428: "Precondition Required", 429: "Too Many Requests",
    500: "Internal Server Error", 501: "Not Implemented", 502: "Bad Gateway", 503: "Service Unavailable", 504: "Gateway Timeout",
}

_NAME_TO_CODE = {}
for _code, _reason in REASONS.items():
    _NAME_TO_CODE[_reason.upper().replace(" ", "_")] = _code          # NOT_FOUND
    _NAME_TO_CODE[_reason.replace(" ", "")] = _code                    # NotFound
    _NAME_TO_CODE["STATUS" + _reason.replace(" ", "").upper()] = _code
_NAME_TO_CODE.update({
    "OK": 200, "HTTP_OK": 200, "MULTI_STATUS": 207, "I_AM_A_TEAPOT": 418, "UNPROCESSABLE_CONTENT": 422,
    "REQUEST_ENTITY_TOO_LARGE": 413, "PAYLOAD_TOO_LARGE": 413, "TOO_MANY_REQUESTS": 429,
})

# framework exception class -> status
EXCEPTION_STATUS = {
    # NestJS
    "BadRequestException": 400, "UnauthorizedException": 401, "ForbiddenException": 403, "NotFoundException": 404,
    "MethodNotAllowedException": 405, "NotAcceptableException": 406, "RequestTimeoutException": 408,
    "ConflictException": 409, "GoneException": 410, "PayloadTooLargeException": 413,
    "UnsupportedMediaTypeException": 415, "UnprocessableEntityException": 422, "InternalServerErrorException": 500,
    "NotImplementedException": 501, "BadGatewayException": 502, "ServiceUnavailableException": 503, "GatewayTimeoutException": 504,
    # Common custom names (Java/Kotlin/C#/Python conventions)
    "EntityNotFoundException": 404, "ResourceNotFoundException": 404, "NoSuchElementException": 404,
    "IllegalArgumentException": 400, "ValidationException": 400, "MethodArgumentNotValidException": 400,
    "ConstraintViolationException": 400, "AccessDeniedException": 403, "AuthenticationException": 401,
    "DuplicateKeyException": 409, "OptimisticLockException": 409, "TimeoutException": 504,
    "PermissionDenied": 403, "NotAuthenticated": 401, "AuthenticationFailed": 401, "ValidationError": 400,
    "Http404": 404, "PermissionError": 403, "KeyError": 400, "ValueError": 400,
    "UnauthorizedError": 401, "ForbiddenError": 403, "NotFoundError": 404, "ConflictError": 409, "BadRequestError": 400,
}

_CODE_RX = re.compile(r"\b([1-5]\d{2})\b")
_STATUS_NAME_RX = re.compile(r"(?:HttpStatus|HttpStatusCode|StatusCodes|http|status|Status)\s*[.:]\s*(?:Status)?([A-Za-z_0-9]+)")


def status_from_name(name: str) -> Optional[int]:
    if not name:
        return None
    n = name.strip()
    m = re.search(r"(?:HTTP_|Status)?([1-5]\d{2})", n)
    if m:
        return int(m.group(1))
    return _NAME_TO_CODE.get(n) or _NAME_TO_CODE.get(n.upper())


def status_from_expr(text: str) -> Optional[int]:
    """Pull an HTTP status out of an expression like `HttpStatus.NOT_FOUND`,
    `http.StatusNotFound`, `status.HTTP_404_NOT_FOUND`, `StatusCodes.Status404NotFound`
    or a bare `404`."""
    if not text:
        return None
    for m in _STATUS_NAME_RX.finditer(text):
        code = status_from_name(m.group(1))
        if code:
            return code
    m = re.search(r"\b(?:HTTP_)?([1-5]\d{2})(?:_[A-Z_]+)?\b", text)
    if m:
        return int(m.group(1))
    return None


def reason(code: int) -> str:
    return REASONS.get(code, "")


def label(code: int) -> str:
    r = reason(code)
    return f"{code} {r}" if r else str(code)
