from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .models import CopyGenerationRequest, Platform, Profile, PropertyCreate, PropertyUpdate, PublishRequest, UserLogin, UserRegister, ValidationRequest
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
    version="0.1.0",
    description="AI-first MVP scaffold for realtor listing creation, feed generation and export.",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=settings.static_dir), name="static")
app.mount("/media", StaticFiles(directory=settings.storage_dir), name="media")


def current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if credentials is None:
        raise HTTPException(status_code=401, detail="Нужна авторизация.")
    try:
        payload = decode_access_token(settings.secret_key, credentials.credentials)
        return service.get_user(payload["sub"])
    except (AuthenticationError, ValueError) as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@app.exception_handler(AuthenticationError)
async def auth_exception_handler(_: Request, exc: AuthenticationError):
    return JSONResponse(status_code=401, content={"detail": str(exc)})


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(settings.static_dir / "index.html")


@app.get("/health")
async def health():
    return {"status": "ok", "environment": settings.environment}


@app.post("/auth/register")
async def register(payload: UserRegister):
    try:
        return service.register(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/auth/login")
async def login(payload: UserLogin):
    try:
        return service.login(payload)
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@app.get("/me/profile")
async def get_profile(user=Depends(current_user)):
    return service.get_profile(user.id)


@app.patch("/me/profile")
async def patch_profile(payload: Profile, user=Depends(current_user)):
    return service.update_profile(user.id, payload)


@app.post("/properties")
async def create_property(payload: PropertyCreate, user=Depends(current_user)):
    return service.create_property(user.id, payload)


@app.get("/properties")
async def list_properties(user=Depends(current_user)):
    return service.list_properties(user.id)


@app.get("/properties/{property_id}")
async def get_property(property_id: str, user=Depends(current_user)):
    try:
        return service.get_snapshot(user.id, property_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.patch("/properties/{property_id}")
async def patch_property(property_id: str, payload: PropertyUpdate, user=Depends(current_user)):
    try:
        return service.update_property(user.id, property_id, payload)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/properties/{property_id}/photos")
async def upload_photos(property_id: str, files: list[UploadFile] = File(...), user=Depends(current_user)):
    try:
        upload_payload = []
        for file in files:
            upload_payload.append((file.filename or "photo.jpg", file.content_type, await file.read()))
        return service.upload_photos(user.id, property_id, upload_payload)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/properties/{property_id}/voice")
async def upload_voice_note(
    property_id: str,
    file: UploadFile = File(...),
    transcript_override: str | None = Form(None),
    user=Depends(current_user),
):
    try:
        return service.upload_voice_note(
            user.id,
            property_id,
            file.filename or "voice.wav",
            file.content_type,
            await file.read(),
            transcript_override,
        )
    except (NotFoundError, ValidationError) as exc:
        status_code = 404 if isinstance(exc, NotFoundError) else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@app.post("/properties/{property_id}/ai/extract")
async def extract_fields(property_id: str, user=Depends(current_user)):
    try:
        return service.extract_fields(user.id, property_id)
    except (NotFoundError, ValidationError) as exc:
        status_code = 404 if isinstance(exc, NotFoundError) else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@app.post("/properties/{property_id}/ai/generate-copy")
async def generate_copy(property_id: str, payload: CopyGenerationRequest, user=Depends(current_user)):
    try:
        return service.generate_copy(user.id, property_id, payload.platforms)
    except (NotFoundError, ValidationError) as exc:
        status_code = 404 if isinstance(exc, NotFoundError) else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@app.post("/properties/{property_id}/validate")
async def validate_property(property_id: str, payload: ValidationRequest, user=Depends(current_user)):
    try:
        return service.validate_property(user.id, property_id, payload.platforms)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/properties/{property_id}/publish")
async def publish_property(property_id: str, payload: PublishRequest, user=Depends(current_user)):
    try:
        return service.publish_property(user.id, property_id, payload.platforms)
    except (NotFoundError, ValidationError) as exc:
        status_code = 404 if isinstance(exc, NotFoundError) else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@app.get("/properties/{property_id}/publication-jobs")
async def publication_jobs(property_id: str, user=Depends(current_user)):
    return service.list_publication_jobs(user.id, property_id)


@app.get("/feeds/{user_id}/{platform}.xml")
async def feed_file(user_id: str, platform: str):
    try:
        platform_enum = Platform(platform)
        path = service.get_feed_path(user_id, platform_enum)
        return FileResponse(path, media_type="application/xml")
    except (ValueError, NotFoundError) as exc:
        status_code = 404 if isinstance(exc, NotFoundError) else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@app.get("/exports/{export_id}.zip")
async def export_file(export_id: str):
    try:
        path = service.get_export_path(export_id)
        return FileResponse(path, media_type="application/zip", filename=Path(path).name)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
