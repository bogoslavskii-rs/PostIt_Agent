from __future__ import annotations

import hashlib
import html
import io
import json
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from PIL import Image, ImageStat
except ImportError:  # pragma: no cover - optional dependency
    Image = None
    ImageStat = None

from .adapters import ListingWithPayload, build_adapters
from .ai import GeneratedCopy, apply_extracted_fields, build_ai_provider, build_stt_provider
from .config import Settings
from .models import CopyGenerationResponse, ExtractionResponse, FeedFileRecord, GenerationRecord, PhotoRecord, Platform, PlatformPayloadRecord, PlatformValidationResult, Profile, PropertyCreate, PropertyRecord, PropertySnapshot, PropertyStatus, PropertyUpdate, PublicationJobRecord, PublishResponse, TokenResponse, UserLogin, UserRegister, UserResponse, VoiceNoteRecord
from .repository import Repository
from .security import create_access_token, hash_password, verify_password


class AuthenticationError(Exception):
    pass


class NotFoundError(Exception):
    pass


class ValidationError(Exception):
    pass


class ApplicationService:
    def __init__(self, settings: Settings, repository: Repository) -> None:
        self.settings = settings
        self.repository = repository
        self.ai_provider = build_ai_provider(settings)
        self.stt_provider = build_stt_provider(settings)
        self.adapters = build_adapters()

    def register(self, payload: UserRegister) -> TokenResponse:
        if self.repository.get_user_by_email(payload.email):
            raise ValidationError("Пользователь с таким e-mail уже существует.")

        now = datetime.utcnow()
        user = UserResponse(
            id=str(uuid.uuid4()),
            email=payload.email,
            phone=payload.phone,
            created_at=now,
        )
        self.repository.create_user(user, hash_password(payload.password))
        self.repository.save_profile(
            user.id,
            Profile(contact_email=payload.email, contact_phone=payload.phone),
        )
        token = create_access_token(self.settings.secret_key, user.id, self.settings.token_ttl_minutes)
        return TokenResponse(access_token=token, user=user)

    def login(self, payload: UserLogin) -> TokenResponse:
        stored = self.repository.get_user_by_email(payload.email)
        if stored is None:
            raise AuthenticationError("Неверный e-mail или пароль.")
        user, password_hash = stored
        if not verify_password(payload.password, password_hash):
            raise AuthenticationError("Неверный e-mail или пароль.")
        token = create_access_token(self.settings.secret_key, user.id, self.settings.token_ttl_minutes)
        return TokenResponse(access_token=token, user=user)

    def get_user(self, user_id: str) -> UserResponse:
        user = self.repository.get_user_by_id(user_id)
        if user is None:
            raise AuthenticationError("Пользователь не найден.")
        return user

    def get_profile(self, user_id: str) -> Profile:
        return self.repository.get_profile(user_id)

    def update_profile(self, user_id: str, profile: Profile) -> Profile:
        self.repository.save_profile(user_id, profile)
        return profile

    def list_properties(self, user_id: str) -> list[PropertyRecord]:
        return self.repository.list_properties(user_id)

    def create_property(self, user_id: str, payload: PropertyCreate) -> PropertyRecord:
        profile = self.repository.get_profile(user_id)
        now = datetime.utcnow()
        property_record = PropertyRecord(
            id=str(uuid.uuid4()),
            user_id=user_id,
            created_at=now,
            updated_at=now,
            **payload.model_dump(mode="python"),
        )
        if not property_record.contacts.contact_phone:
            property_record.contacts = property_record.contacts.model_copy(update={"contact_phone": profile.contact_phone})
        if not property_record.contacts.contact_email:
            property_record.contacts = property_record.contacts.model_copy(update={"contact_email": profile.contact_email})
        self.repository.save_property(property_record)
        return property_record

    def get_snapshot(self, user_id: str, property_id: str) -> PropertySnapshot:
        property_record = self.repository.get_property(user_id, property_id)
        if property_record is None:
            raise NotFoundError("Объект не найден.")
        return PropertySnapshot(
            property=property_record,
            profile=self.repository.get_profile(user_id),
            photos=self.repository.list_photos(user_id, property_id),
            voice_notes=self.repository.list_voice_notes(user_id, property_id),
        )

    def update_property(self, user_id: str, property_id: str, patch: PropertyUpdate) -> PropertySnapshot:
        snapshot = self.get_snapshot(user_id, property_id)
        property_record = snapshot.property
        payload = patch.model_dump(exclude_unset=True, mode="python")
        merged = property_record.model_dump(mode="python")

        for nested in ("address", "features", "contacts"):
            if payload.get(nested) is not None:
                merged[nested].update(payload.pop(nested))
        merged.update(payload)
        merged["updated_at"] = datetime.utcnow()

        updated = PropertyRecord.model_validate(merged)
        self.repository.save_property(updated)
        snapshot.property = updated
        return snapshot

    def upload_photos(self, user_id: str, property_id: str, files: list[tuple[str, str | None, bytes]]) -> PropertySnapshot:
        snapshot = self.get_snapshot(user_id, property_id)
        existing = snapshot.photos
        hashes = {photo.content_hash for photo in existing}
        created: list[PhotoRecord] = []

        photos_dir = self.settings.storage_dir / user_id / property_id / "photos"
        photos_dir.mkdir(parents=True, exist_ok=True)

        for index, (filename, mime_type, content) in enumerate(files, start=len(existing)):
            if not content:
                continue
            extension = Path(filename).suffix or ".bin"
            safe_name = f"{uuid.uuid4().hex}{extension}"
            path = photos_dir / safe_name
            path.write_bytes(content)
            content_hash = hashlib.sha1(content).hexdigest()
            quality_score, warnings = analyze_photo(path, len(content), content_hash in hashes)
            hashes.add(content_hash)
            relative_path = str(path.relative_to(self.settings.storage_dir))
            photo = PhotoRecord(
                id=str(uuid.uuid4()),
                property_id=property_id,
                user_id=user_id,
                original_name=filename,
                mime_type=mime_type or "application/octet-stream",
                size_bytes=len(content),
                relative_path=relative_path,
                public_url=self.public_media_url(relative_path),
                content_hash=content_hash,
                order_index=index,
                is_main=False,
                quality_score=quality_score,
                warnings=warnings,
                created_at=datetime.utcnow(),
            )
            self.repository.save_photo(photo)
            created.append(photo)

        snapshot.photos = self.repository.list_photos(user_id, property_id)
        if snapshot.photos:
            best_photo_id = max(snapshot.photos, key=lambda item: item.quality_score).id
            snapshot.photos = [
                photo.model_copy(update={"is_main": photo.id == best_photo_id}) for photo in snapshot.photos
            ]
            self.repository.replace_photos(snapshot.photos)
        return snapshot

    def upload_voice_note(
        self,
        user_id: str,
        property_id: str,
        filename: str,
        mime_type: str | None,
        content: bytes,
        transcript_override: str | None = None,
    ) -> PropertySnapshot:
        snapshot = self.get_snapshot(user_id, property_id)
        voice_dir = self.settings.storage_dir / user_id / property_id / "voice"
        voice_dir.mkdir(parents=True, exist_ok=True)
        extension = Path(filename).suffix or ".wav"
        safe_name = f"{uuid.uuid4().hex}{extension}"
        path = voice_dir / safe_name
        path.write_bytes(content)
        transcript = self.stt_provider.transcribe(path, transcript_override)
        relative_path = str(path.relative_to(self.settings.storage_dir))
        voice_note = VoiceNoteRecord(
            id=str(uuid.uuid4()),
            property_id=property_id,
            user_id=user_id,
            original_name=filename,
            mime_type=mime_type or "application/octet-stream",
            relative_path=relative_path,
            public_url=self.public_media_url(relative_path),
            transcript=transcript,
            status="transcribed" if transcript else "uploaded",
            created_at=datetime.utcnow(),
        )
        self.repository.save_voice_note(voice_note)
        snapshot.property = snapshot.property.model_copy(
            update={"transcript": transcript, "updated_at": datetime.utcnow()}
        )
        self.repository.save_property(snapshot.property)
        snapshot.voice_notes = self.repository.list_voice_notes(user_id, property_id)
        return snapshot

    def extract_fields(self, user_id: str, property_id: str) -> ExtractionResponse:
        snapshot = self.get_snapshot(user_id, property_id)
        if not snapshot.property.transcript:
            raise ValidationError("Сначала загрузите голосовую заметку или текст транскрипта.")
        fields = self.ai_provider.extract_fields(snapshot)
        snapshot = apply_extracted_fields(snapshot, fields)
        snapshot.property = snapshot.property.model_copy(update={"updated_at": datetime.utcnow()})
        self.repository.save_property(snapshot.property)
        self.repository.save_generation(
            GenerationRecord(
                id=str(uuid.uuid4()),
                property_id=property_id,
                user_id=user_id,
                generation_type="field_extraction",
                input_hash=sha1_text(snapshot.property.transcript or ""),
                output_json={key: value.model_dump(mode="json") for key, value in fields.items()},
                created_at=datetime.utcnow(),
            )
        )
        return ExtractionResponse(fields=fields, property=snapshot.property)

    def generate_copy(self, user_id: str, property_id: str, platforms: list[Platform]) -> CopyGenerationResponse:
        snapshot = self.get_snapshot(user_id, property_id)
        generated = self.ai_provider.generate_copy(snapshot, platforms)
        snapshot.property = snapshot.property.model_copy(
            update={
                "title_variants": generated.titles,
                "title_base": generated.titles[0] if generated.titles else snapshot.property.title_base,
                "description_short": generated.description_short,
                "description_base": generated.description_full,
                "highlights": generated.highlights,
                "status": PropertyStatus.reviewed,
                "updated_at": datetime.utcnow(),
            }
        )
        self.repository.save_property(snapshot.property)
        self.repository.save_generation(
            GenerationRecord(
                id=str(uuid.uuid4()),
                property_id=property_id,
                user_id=user_id,
                generation_type="copy_generation",
                input_hash=sha1_text(json.dumps(snapshot.property.model_dump(mode="json"), ensure_ascii=False)),
                output_json={
                    "titles": generated.titles,
                    "description_short": generated.description_short,
                    "description_full": generated.description_full,
                    "highlights": generated.highlights,
                    "platform_copy": {
                        key: value.model_dump(mode="json") for key, value in generated.platform_copy.items()
                    },
                },
                created_at=datetime.utcnow(),
            )
        )
        return CopyGenerationResponse(
            titles=generated.titles,
            description_short=generated.description_short,
            description_full=generated.description_full,
            highlights=generated.highlights,
            platform_copy=generated.platform_copy,
            property=snapshot.property,
        )

    def validate_property(self, user_id: str, property_id: str, platforms: list[Platform]) -> list[PlatformValidationResult]:
        snapshot = self.get_snapshot(user_id, property_id)
        return [self.adapters[platform].validate(snapshot) for platform in platforms]

    def publish_property(self, user_id: str, property_id: str, platforms: list[Platform]) -> PublishResponse:
        snapshot = self.get_snapshot(user_id, property_id)
        generated = self.ai_provider.generate_copy(snapshot, platforms)
        if not snapshot.property.description_base:
            snapshot.property = snapshot.property.model_copy(
                update={
                    "title_variants": generated.titles,
                    "title_base": generated.titles[0] if generated.titles else snapshot.property.title_base,
                    "description_short": generated.description_short,
                    "description_base": generated.description_full,
                    "highlights": generated.highlights,
                    "updated_at": datetime.utcnow(),
                }
            )
            self.repository.save_property(snapshot.property)

        validation_results = {result.platform: result for result in self.validate_property(user_id, property_id, platforms)}
        export_id = str(uuid.uuid4())
        export_url = self.public_export_url(export_id)
        zip_path = self.settings.exports_dir / f"{export_id}.zip"
        jobs: list[PublicationJobRecord] = []

        successful_payloads: dict[Platform, PlatformPayloadRecord] = {}
        for platform in platforms:
            validation = validation_results[platform]
            adapter = self.adapters[platform]
            if not validation.ready:
                job = PublicationJobRecord(
                    id=str(uuid.uuid4()),
                    property_id=property_id,
                    user_id=user_id,
                    platform=platform,
                    channel=validation.channel,
                    status="blocked",
                    export_url=export_url,
                    errors=validation.errors,
                    notes=validation.warnings,
                    created_at=datetime.utcnow(),
                )
                self.repository.save_publication_job(job)
                jobs.append(job)
                continue

            copy = generated.platform_copy.get(platform.value)
            if copy is None:
                raise ValidationError(f"Не удалось собрать платформенный текст для {platform.value}.")
            payload_dict = adapter.build_payload(snapshot, copy, validation)
            payload_dict["highlights"] = copy.highlights
            payload = PlatformPayloadRecord(
                id=str(uuid.uuid4()),
                property_id=property_id,
                user_id=user_id,
                platform=platform,
                title=copy.title,
                description=copy.description,
                validation=validation,
                payload=payload_dict,
                created_at=datetime.utcnow(),
            )
            self.repository.save_platform_payload(payload)
            successful_payloads[platform] = payload

        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("property.json", json.dumps(snapshot.property.model_dump(mode="json"), ensure_ascii=False, indent=2))
            archive.writestr(
                "listing_copy.txt",
                build_listing_text(
                    snapshot.property.title_base or generated.titles[0],
                    snapshot.property.description_base or generated.description_full,
                    snapshot.property.highlights or generated.highlights,
                ),
            )
            archive.writestr(
                "listing_copy.docx",
                build_docx(
                    snapshot.property.title_base or generated.titles[0],
                    snapshot.property.description_base or generated.description_full,
                    snapshot.property.highlights or generated.highlights,
                ),
            )
            for photo in snapshot.photos:
                source = self.settings.storage_dir / photo.relative_path
                if source.exists():
                    archive.write(source, arcname=f"photos/{photo.original_name}")

            for platform, payload in successful_payloads.items():
                adapter = self.adapters[platform]
                listing = ListingWithPayload(snapshot=snapshot, payload=payload)
                for arcname, data in adapter.build_export_entries(listing).items():
                    archive.writestr(arcname, data)

        for platform, payload in successful_payloads.items():
            adapter = self.adapters[platform]
            listings = self._listings_for_feed(user_id, platform)
            feed_body = adapter.build_feed(listings)
            feed_dir = self.settings.feeds_dir / user_id
            feed_dir.mkdir(parents=True, exist_ok=True)
            feed_path = feed_dir / f"{platform.value}.xml"
            feed_path.write_text(feed_body, encoding="utf-8")
            feed_url = self.public_feed_url(user_id, platform)
            self.repository.save_feed_file(
                FeedFileRecord(
                    id=str(uuid.uuid4()),
                    user_id=user_id,
                    platform=platform,
                    path=str(feed_path),
                    feed_url=feed_url,
                    checksum=sha1_text(feed_body),
                    created_at=datetime.utcnow(),
                )
            )
            job = PublicationJobRecord(
                id=str(uuid.uuid4()),
                property_id=property_id,
                user_id=user_id,
                platform=platform,
                channel=payload.validation.channel,
                status="ready_for_publish",
                feed_url=feed_url,
                export_url=export_url,
                notes=payload.validation.warnings,
                created_at=datetime.utcnow(),
            )
            self.repository.save_publication_job(job)
            jobs.append(job)

        if successful_payloads:
            snapshot.property = snapshot.property.model_copy(
                update={"status": PropertyStatus.published, "updated_at": datetime.utcnow()}
            )
            self.repository.save_property(snapshot.property)

        return PublishResponse(
            property=snapshot.property,
            validations=list(validation_results.values()),
            jobs=sorted(jobs, key=lambda item: item.created_at, reverse=True),
            export_url=export_url,
        )

    def list_publication_jobs(self, user_id: str, property_id: str) -> list[PublicationJobRecord]:
        return self.repository.list_publication_jobs(user_id, property_id)

    def get_feed_path(self, user_id: str, platform: Platform) -> Path:
        feed = self.repository.get_feed_file(user_id, platform)
        if feed is None:
            raise NotFoundError("Фид для пользователя и площадки пока не создан.")
        return Path(feed.path)

    def get_export_path(self, export_id: str) -> Path:
        export_path = self.settings.exports_dir / f"{export_id}.zip"
        if not export_path.exists():
            raise NotFoundError("Экспортный архив не найден.")
        return export_path

    def public_media_url(self, relative_path: str) -> str:
        return f"{self.settings.public_base_url.rstrip('/')}/media/{relative_path}"

    def public_feed_url(self, user_id: str, platform: Platform) -> str:
        return f"{self.settings.public_base_url.rstrip('/')}/feeds/{user_id}/{platform.value}.xml"

    def public_export_url(self, export_id: str) -> str:
        return f"{self.settings.public_base_url.rstrip('/')}/exports/{export_id}.zip"

    def _listings_for_feed(self, user_id: str, platform: Platform) -> list[ListingWithPayload]:
        payloads = self.repository.list_platform_payloads(user_id, platform)
        listings: list[ListingWithPayload] = []
        for payload in payloads:
            try:
                snapshot = self.get_snapshot(user_id, payload.property_id)
            except NotFoundError:
                continue
            listings.append(ListingWithPayload(snapshot=snapshot, payload=payload))
        return listings


def sha1_text(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def analyze_photo(path: Path, size_bytes: int, is_duplicate: bool) -> tuple[float, list[str]]:
    score = 0.45
    warnings: list[str] = []

    if size_bytes < 150_000:
        score -= 0.12
        warnings.append("Небольшой размер файла: фото может оказаться пережатым.")
    else:
        score += 0.08

    if is_duplicate:
        score -= 0.35
        warnings.append("Похоже на дубликат уже загруженной фотографии.")

    if Image is not None:
        try:
            with Image.open(path) as image:
                width, height = image.size
                if min(width, height) < 600:
                    warnings.append("Низкое разрешение: площадки могут отдать фото в конец галереи.")
                    score -= 0.08
                else:
                    score += 0.12
                grayscale = image.convert("L")
                if ImageStat is not None:
                    brightness = ImageStat.Stat(grayscale).mean[0]
                    if brightness < 45:
                        warnings.append("Фото выглядит слишком темным.")
                        score -= 0.08
                    elif brightness > 225:
                        warnings.append("Фото выглядит пересвеченным.")
                        score -= 0.04
                    else:
                        score += 0.1
        except Exception:
            warnings.append("Не удалось проанализировать изображение полностью, использована базовая эвристика.")
    else:
        warnings.append("Pillow не установлен: оценка фото выполнена по упрощенным правилам.")

    return max(0.0, min(score, 0.99)), warnings


def build_listing_text(title: str, description: str, highlights: list[str]) -> str:
    lines = [title, "", description.strip(), "", "Преимущества:"]
    lines.extend(f"- {item}" for item in highlights)
    return "\n".join(lines).strip() + "\n"


def build_docx(title: str, description: str, highlights: list[str]) -> bytes:
    document_body = [
        paragraph(title, bold=True),
        paragraph(description),
        paragraph("Преимущества:", bold=True),
    ]
    document_body.extend(paragraph(item) for item in highlights)
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        + "".join(document_body)
        + (
            '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
            '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/>'
            "</w:sectPr>"
        )
        + "</w:body></w:document>"
    )

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        archive.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/>'
            "</Relationships>",
        )
        archive.writestr("word/document.xml", document_xml)
    return output.getvalue()


def paragraph(text: str, bold: bool = False) -> str:
    escaped = html.escape(text)
    if bold:
        run = f"<w:r><w:rPr><w:b/></w:rPr><w:t>{escaped}</w:t></w:r>"
    else:
        run = f"<w:r><w:t>{escaped}</w:t></w:r>"
    return f"<w:p>{run}</w:p>"


def bootstrap_repository(settings: Settings) -> Repository:
    repository = Repository(settings.database_path)
    repository.init()
    return repository
