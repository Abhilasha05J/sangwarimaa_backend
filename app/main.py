"""
Entry point of your fastapi Application - MAIN CONTROL ROOM :)
here:
    --FastApi app is created
    --Swagger documentation is configured
    --CORS is enabled
    --Exception handling is registered
    --API routes are attached
    --Startup and shutdown events are defined

"""
from contextlib import asynccontextmanager
from fastapi import FastAPI,Request
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.core.exceptions import AppException, app_exception_handler, generic_exception_handler
from app.api.v1.routes import auth, women, asha, admin
from fastapi.security import HTTPBearer
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from app.middleware.rate_limit import RateLimitMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialize DB tables, Redis, etc.
    if settings.DEBUG:
        print("⚠️  WARNING: DEBUG mode is ON — disable before going live!")
        from app.core.database import engine, Base
        from app.models import models  # noqa
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        print("✅ Database tables verified")
    else:
        print("🔒 Production mode active")
        try:
            settings.validate_production()
            print("✅ Security config validated")
        except AssertionError as e:
            print(f"❌ Security config error: {e}")
            raise
    yield
    # Shutdown
    print("👋 Shutting down...")

security = HTTPBearer()

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.VERSION,
    openapi_url=f"{settings.API_V1_PREFIX}/openapi.json" if settings.DEBUG else None,
    docs_url=f"{settings.API_V1_PREFIX}/docs" if settings.DEBUG else None,
    redoc_url=f"{settings.API_V1_PREFIX}/redoc" if settings.DEBUG else None,
    lifespan=lifespan,
    swagger_ui_parameters={"persistAuthorization": True},

)

# swagger securtiy
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    openapi_schema = get_openapi(
        title=settings.APP_NAME,
        version=settings.VERSION,
        routes=app.routes,
    )
    openapi_schema["components"]["securitySchemes"] = {
        "BearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
        }
    }
    openapi_schema["security"] = [{"BearerAuth": []}]
    app.openapi_schema = openapi_schema
    return app.openapi_schema

app.openapi = custom_openapi


# ── Rate Limiting ─────────────
app.add_middleware(RateLimitMiddleware)

# ── CORS ──────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
)

# ── Security Headers ─────────────
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response
 
# Block sensitive paths
@app.middleware("http")
async def block_sensitive_paths(request: Request, call_next):
    blocked = ["/.env", "/.git", "/config", "/__pycache__"]
    if any(request.url.path.startswith(p) for p in blocked):
        return JSONResponse(status_code=404, content={"detail": "Not found"})
    return await call_next(request)

# ── Exception Handlers ────────────────────────────────────────────────────────
app.add_exception_handler(AppException, app_exception_handler)
app.add_exception_handler(Exception, generic_exception_handler)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth.router, prefix=settings.API_V1_PREFIX)
app.include_router(women.router, prefix=settings.API_V1_PREFIX)
app.include_router(asha.router, prefix=settings.API_V1_PREFIX) 
app.include_router(admin.router, prefix=settings.API_V1_PREFIX)


@app.get("/", tags=["Health"])
async def health_check():
    return {"status": "ok", "version": settings.VERSION}
