from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .models import (
    CopyGenerationRequest,
    MediaOrderRequest,
    MediaPatchRequest,
    Platform,
    Profile,
    PropertyCreate,
    PropertyUpdate,
    PublicationStatusUpdateRequest,
    PublishRequest,
    UserLogin,
    UserRegister,
    ValidationRequest,
)
from .security import decode_access_token
from .services import ApplicationService, AuthenticationError, NotFoundError, ValidationError, bootstrap_repository


settings = get_settings()
repository = bootstrap_repository(settings)
service = ApplicationService(settings, repository)
security = HTTPBearer(auto_error=False)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_directories()
    yield


app = FastAPI(
    title="PostIt Agent",
    version="0.2.0",
    description="Local MVP for realtor listing creation, AI extraction, feeds and assisted export.",
    lifespan=lifespan,
    debug=settings.debug,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list() or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=settings.static_dir), name="static")
app.mount("/media", StaticFiles(directory=settings.storage_dir), name="media")


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


def error_response(request: Request, status_code: int, code: str, message: str, details: list[str] | None = None) -> JSONResponse:
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": details or [],
            },
            "request_id": request_id,
        },
    )


@app.exception_handler(AuthenticationError)
async def auth_exception_handler(request: Request, exc: AuthenticationError):
    return error_response(request, 401, "authentication_failed", str(exc))


@app.exception_handler(ValidationError)
async def validation_exception_handler(request: Request, exc: ValidationError):
    return error_response(request, 400, "validation_error", str(exc))


@app.exception_handler(NotFoundError)
async def not_found_exception_handler(request: Request, exc: NotFoundError):
    return error_response(request, 404, "not_found", str(exc))


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    message = exc.detail if isinstance(exc.detail, str) else "HTTP error"
    return error_response(request, exc.status_code, "http_error", message)


@app.exception_handler(Exception)
async def unexpected_exception_handler(request: Request, exc: Exception):
    if settings.environment == "development":
        return error_response(request, 500, "internal_error", f"{type(exc).__name__}: {exc}")
    return error_response(request, 500, "internal_error", "Внутренняя ошибка сервиса.")


def current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if credentials is None:
        raise HTTPException(status_code=401, detail="Нужна авторизация.")
    try:
        payload = decode_access_token(settings.secret_key, credentials.credentials)
        return service.get_user(payload["sub"])
    except (AuthenticationError, ValueError) as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(settings.static_dir / "index.html")


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "environment": settings.environment,
        "timestamp": datetime.now(UTC).date().isoformat(),
    }


@app.get("/ready")
async def ready():
    checks = {
        "database": repository.ping(),
        "storage": settings.storage_dir.exists(),
        "static": settings.static_dir.exists(),
    }
    status = "ready" if all(checks.values()) else "degraded"
    return {"status": status, "checks": checks}


@app.post("/auth/register")
@app.post("/api/v1/auth/register")
async def register(payload: UserRegister):
    return service.register(payload)


@app.post("/auth/login")
@app.post("/api/v1/auth/login")
async def login(payload: UserLogin):
    return service.login(payload)


@app.get("/api/v1/auth/me")
async def auth_me(user=Depends(current_user)):
    return user


@app.get("/me/profile")
async def get_profile(user=Depends(current_user)):
    return service.get_profile(user.id)


@app.patch("/me/profile")
async def patch_profile(payload: Profile, user=Depends(current_user)):
    return service.update_profile(user.id, payload)


@app.post("/properties")
@app.post("/api/v1/properties")
async def create_property(payload: PropertyCreate, user=Depends(current_user)):
    return service.create_property(user.id, payload)


@app.get("/properties")
@app.get("/api/v1/properties")
async def list_properties(user=Depends(current_user)):
    return service.list_properties(user.id)


@app.get("/properties/{property_id}")
@app.get("/api/v1/properties/{property_id}")
async def get_property(property_id: str, user=Depends(current_user)):
    return service.get_snapshot(user.id, property_id)


@app.patch("/properties/{property_id}")
@app.patch("/api/v1/properties/{property_id}")
async def patch_property(property_id: str, payload: PropertyUpdate, user=Depends(current_user)):
    return service.update_property(user.id, property_id, payload)


@app.delete("/api/v1/properties/{property_id}", status_code=204)
async def delete_property(property_id: str, user=Depends(current_user)) -> Response:
    service.delete_property(user.id, property_id)
    return Response(status_code=204)


@app.post("/properties/{property_id}/photos")
@app.post("/api/v1/properties/{property_id}/media")
async def upload_photos(property_id: str, files: list[UploadFile] = File(...), user=Depends(current_user)):
    upload_payload = []
    for file in files:
        upload_payload.append((file.filename or "photo.jpg", file.content_type, await file.read()))
    return service.upload_photos(user.id, property_id, upload_payload)


@app.get("/api/v1/properties/{property_id}/media")
async def list_media(property_id: str, user=Depends(current_user)):
    return service.list_media(user.id, property_id)


@app.patch("/api/v1/properties/{property_id}/media/order")
async def patch_media_order(property_id: str, payload: MediaOrderRequest, user=Depends(current_user)):
    return service.reorder_media(user.id, property_id, payload)


@app.patch("/api/v1/properties/{property_id}/media/{media_id}")
async def patch_media(property_id: str, media_id: str, payload: MediaPatchRequest, user=Depends(current_user)):
    return service.patch_media(user.id, property_id, media_id, payload)


@app.delete("/api/v1/properties/{property_id}/media/{media_id}")
async def delete_media(property_id: str, media_id: str, user=Depends(current_user)):
    return service.delete_media(user.id, property_id, media_id)


@app.post("/properties/{property_id}/voice")
@app.post("/api/v1/properties/{property_id}/transcriptions")
async def upload_voice_note(
    property_id: str,
    file: UploadFile = File(...),
    transcript_override: str | None = Form(None),
    user=Depends(current_user),
):
    return service.upload_voice_note(
        user.id,
        property_id,
        file.filename or "voice.wav",
        file.content_type,
        await file.read(),
        transcript_override,
    )


@app.post("/properties/{property_id}/ai/extract")
@app.post("/api/v1/properties/{property_id}/extract")
async def extract_fields(property_id: str, user=Depends(current_user)):
    return service.extract_fields(user.id, property_id)


@app.post("/api/v1/properties/{property_id}/analyze-photos")
async def analyze_photos(property_id: str, user=Depends(current_user)):
    return service.reanalyze_photos(user.id, property_id)


@app.get("/api/v1/properties/{property_id}/evidence")
async def list_evidence(property_id: str, user=Depends(current_user)):
    return service.list_evidence(user.id, property_id)


@app.post("/api/v1/properties/{property_id}/evidence/{evidence_id}/confirm")
async def confirm_evidence(property_id: str, evidence_id: str, user=Depends(current_user)):
    return service.confirm_evidence(user.id, property_id, evidence_id)


@app.post("/api/v1/properties/{property_id}/evidence/{evidence_id}/reject")
async def reject_evidence(property_id: str, evidence_id: str, user=Depends(current_user)):
    return service.reject_evidence(user.id, property_id, evidence_id)


@app.post("/properties/{property_id}/ai/generate-copy")
@app.post("/api/v1/properties/{property_id}/generate-copy")
async def generate_copy(property_id: str, payload: CopyGenerationRequest, user=Depends(current_user)):
    return service.generate_copy(user.id, property_id, payload.platforms)


@app.post("/properties/{property_id}/validate")
@app.post("/api/v1/properties/{property_id}/validate")
async def validate_property(property_id: str, payload: ValidationRequest, user=Depends(current_user)):
    return service.validate_property(user.id, property_id, payload.platforms)


@app.post("/properties/{property_id}/publish")
@app.post("/api/v1/properties/{property_id}/publications")
async def publish_property(property_id: str, payload: PublishRequest, user=Depends(current_user)):
    return service.publish_property(user.id, property_id, payload.platforms)


@app.get("/properties/{property_id}/publication-jobs")
@app.get("/api/v1/properties/{property_id}/publications")
async def publication_jobs(property_id: str, user=Depends(current_user)):
    return service.list_publication_jobs(user.id, property_id)


@app.get("/api/v1/publications/{publication_id}")
async def publication_detail(publication_id: str, user=Depends(current_user)):
    return service.get_publication_job(user.id, publication_id)


@app.post("/api/v1/publications/{publication_id}/prepare")
async def prepare_publication(publication_id: str, user=Depends(current_user)):
    return service.get_publication_job(user.id, publication_id)


@app.post("/api/v1/publications/{publication_id}/publish")
async def publish_publication(publication_id: str, user=Depends(current_user)):
    return service.get_publication_job(user.id, publication_id)


@app.post("/api/v1/publications/{publication_id}/mark-published")
async def mark_publication_published(
    publication_id: str,
    payload: PublicationStatusUpdateRequest,
    user=Depends(current_user),
):
    return service.mark_publication_published(user.id, publication_id, payload)


@app.post("/api/v1/publications/{publication_id}/deactivate")
async def deactivate_publication(
    publication_id: str,
    payload: PublicationStatusUpdateRequest,
    user=Depends(current_user),
):
    return service.deactivate_publication(user.id, publication_id, payload)


@app.get("/feeds/{user_id}/{platform}.xml")
async def feed_file(user_id: str, platform: str):
    platform_enum = Platform(platform)
    path = service.get_feed_path(user_id, platform_enum)
    return FileResponse(path, media_type="application/xml")


@app.post("/api/v1/properties/{property_id}/exports")
async def build_export(property_id: str, payload: PublishRequest, user=Depends(current_user)):
    response = service.publish_property(user.id, property_id, payload.platforms)
    if not response.export_url:
        raise HTTPException(status_code=400, detail="Экспортный архив не был создан.")
    export_id = Path(response.export_url).stem
    return service.get_export_record(export_id)


@app.get("/exports/{export_id}.zip")
async def export_file(export_id: str):
    path = service.get_export_path(export_id)
    return FileResponse(path, media_type="application/zip", filename=Path(path).name)


@app.get("/api/v1/exports/{export_id}")
async def export_info(export_id: str):
    return service.get_export_record(export_id)
