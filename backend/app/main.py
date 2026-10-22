import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded

from app.config import settings
from app.limits import OCR_UPLOADS_PER_HOUR, limiter
from app.routers import account, auth, expenses, household, reports, taxonomy
from app import models

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger("expenselens")
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    Path(settings.storage_dir).mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="ExpenseLens API", version="0.1.0", lifespan=lifespan,
              docs_url="/api/docs" if settings.environment != "production" else None)
app.state.limiter = limiter
@app.exception_handler(RateLimitExceeded)
async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    if request.url.path == "/api/documents/extract":
        detail = (
            f"Hai raggiunto il limite di {OCR_UPLOADS_PER_HOUR} analisi OCR all'ora. "
            "Attendi prima di caricare un altro documento."
        )
    else:
        detail = "Hai inviato troppe richieste in poco tempo. Riprova tra poco."
    return JSONResponse(status_code=429, content={"detail": detail})


app.add_middleware(CORSMiddleware, allow_origins=[settings.frontend_origin],
                   allow_credentials=True, allow_methods=["GET", "POST", "PUT", "DELETE"],
                   allow_headers=["Content-Type", "X-CSRF-Token"])
app.include_router(auth.router)
app.include_router(account.router)
app.include_router(household.router)
app.include_router(expenses.router)
app.include_router(reports.router)
app.include_router(taxonomy.router)


@app.middleware("http")
async def security_headers(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(self), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = "default-src 'self'; frame-ancestors 'none'; base-uri 'self'"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    if settings.environment == "production" and request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    return response


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    logger.error("Unhandled request error", extra={
        "path": request.url.path, "error_type": type(exc).__name__,
    })
    return JSONResponse(status_code=500, content={"detail": "Errore interno. Riprova più tardi."})


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
