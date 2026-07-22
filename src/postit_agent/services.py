from __future__ import annotations

import hashlib
import html
import io
import json
import mimetypes
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    from PIL import Image, ImageStat
except ImportError:  # pragma: no cover - optional dependency
    Image = None
    ImageStat = None

from .adapters import ListingWithPayload, build_adapters
from .ai import GeneratedCopy, build_ai_provider, build_stt_provider
from .config import Settings
from .models import CopyGenerationResponse, EvidenceSource, ExportRecord, ExtractionResponse, FeedFileRecord, GenerationRecord, MediaOrderRequest, MediaPatchRequest, PhotoRecord, Platform, PlatformPayloadRecord, PlatformValidationResult, Profile, PropertyCreate, PropertyFieldEvidence, PropertyRecord, PropertySnapshot, PropertyStatus, PropertyUpdate, PublicationJobRecord, PublicationStatusUpdateRequest, PublishResponse, TokenResponse, UserLogin, UserRegister, UserResponse, VoiceNoteRecord
from .repository import Repository
from .security import create_access_token, hash_password, verify_password


class AuthenticationError(Exception):
    pass


class NotFoundError(Exception):
    pass


class ValidationError(Exception):
    pass


CRITICAL_AI_FIELDS = {
    "city",
    "street",
    "house",
    "price",
    "rooms",
    "area_total",
    "floor",
    "floors_total",
}


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

        now = datetime.now(UTC)
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
        token = create_access_token(self.settings.secret_key, user.id, self.settings.access_token_expire_minutes)
        return TokenResponse(access_token=token, user=user)

    def login(self, payload: UserLogin) -> TokenResponse:
        stored = self.repository.get_user_by_email(payload.email)
        if stored is None:
            raise AuthenticationError("Неверный e-mail или пароль.")
        user, password_hash = stored
        if not verify_password(payload.password, password_hash):
            raise AuthenticationError("Неверный e-mail или пароль.")
        token = create_access_token(self.settings.secret_key, user.id, self.settings.access_token_expire_minutes)
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
        now = datetime.now(UTC)
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
            evidence=self.repository.list_evidence(user_id, property_id),
        )

    def delete_property(self, user_id: str, property_id: str) -> None:
        snapshot = self.get_snapshot(user_id, property_id)
        property_root = self.settings.storage_dir / user_id / property_id
        self.repository.delete_property(user_id, property_id)
        if property_root.exists():
            for path in sorted(property_root.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink(missing_ok=True)
                elif path.is_dir():
                    path.rmdir()
            property_root.rmdir()

    def update_property(self, user_id: str, property_id: str, patch: PropertyUpdate) -> PropertySnapshot:
        snapshot = self.get_snapshot(user_id, property_id)
        property_record = snapshot.property
        payload = patch.model_dump(exclude_unset=True, mode="python")
        merged = property_record.model_dump(mode="python")
        touched_fields = flatten_patch_fields(payload)

        for nested in ("address", "features", "contacts"):
            if payload.get(nested) is not None:
                merged[nested].update(payload.pop(nested))
        merged.update(payload)
        merged["updated_at"] = datetime.now(UTC)

        updated = PropertyRecord.model_validate(merged)
        self.repository.save_property(updated)
        if touched_fields:
            self.repository.reject_pending_evidence(user_id, property_id, touched_fields)
        snapshot.property = updated
        snapshot.evidence = self.repository.list_evidence(user_id, property_id)
        return snapshot

    def upload_photos(self, user_id: str, property_id: str, files: list[tuple[str, str | None, bytes]]) -> PropertySnapshot:
        snapshot = self.get_snapshot(user_id, property_id)
        existing = snapshot.photos
        hashes = {photo.content_hash for photo in existing}

        photos_dir = self.settings.storage_dir / user_id / property_id / "photos"
        photos_dir.mkdir(parents=True, exist_ok=True)

        for index, (filename, mime_type, content) in enumerate(files, start=len(existing)):
            if not content:
                continue
            self._validate_upload(filename, mime_type, content, allowed=self.settings.allowed_image_formats_set())
            extension = self._safe_extension(filename, fallback=".bin")
            safe_name = f"{uuid.uuid4().hex}{extension}"
            path = photos_dir / safe_name
            path.write_bytes(content)
            content_hash = hashlib.sha1(content).hexdigest()
            quality_score, warnings, analysis = analyze_photo(path, len(content), content_hash in hashes)
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
                analysis=analysis,
                created_at=datetime.now(UTC),
            )
            self.repository.save_photo(photo)

        return self.reanalyze_photos(user_id, property_id)

    def list_media(self, user_id: str, property_id: str) -> dict[str, list[Any]]:
        snapshot = self.get_snapshot(user_id, property_id)
        return {
            "photos": sorted(snapshot.photos, key=lambda item: (item.order_index, item.created_at)),
            "voice_notes": snapshot.voice_notes,
        }

    def reorder_media(self, user_id: str, property_id: str, payload: MediaOrderRequest) -> PropertySnapshot:
        snapshot = self.get_snapshot(user_id, property_id)
        photo_map = {photo.id: photo for photo in snapshot.photos}
        ordered: list[PhotoRecord] = []
        seen_ids: set[str] = set()
        for item in payload.items:
            photo = photo_map.get(item.media_id)
            if photo is None:
                continue
            seen_ids.add(photo.id)
            ordered.append(photo.model_copy(update={"order_index": item.order_index}))
        for photo in snapshot.photos:
            if photo.id not in seen_ids:
                ordered.append(photo.model_copy(update={"order_index": len(ordered)}))

        cover_id = payload.cover_media_id
        if cover_id is None and ordered:
            existing_cover = next((photo.id for photo in ordered if photo.is_main), None)
            cover_id = existing_cover or max(ordered, key=lambda item: item.quality_score).id

        ordered = [
            photo.model_copy(update={"is_main": photo.id == cover_id}) for photo in sorted(ordered, key=lambda item: item.order_index)
        ]
        self.repository.replace_photos(ordered)
        return self.get_snapshot(user_id, property_id)

    def patch_media(self, user_id: str, property_id: str, media_id: str, payload: MediaPatchRequest) -> PropertySnapshot:
        snapshot = self.get_snapshot(user_id, property_id)
        photo = self.repository.get_photo(user_id, property_id, media_id)
        if photo is None:
            raise NotFoundError("Медиафайл не найден.")

        updated_photos: list[PhotoRecord] = []
        for item in snapshot.photos:
            if item.id == media_id:
                updated = item.model_copy(
                    update={
                        "order_index": payload.order_index if payload.order_index is not None else item.order_index,
                        "is_main": payload.is_main if payload.is_main is not None else item.is_main,
                    }
                )
                updated_photos.append(updated)
            else:
                updated_photos.append(item.model_copy(update={"is_main": False}) if payload.is_main else item)
        self.repository.replace_photos(updated_photos)
        return self.get_snapshot(user_id, property_id)

    def delete_media(self, user_id: str, property_id: str, media_id: str) -> PropertySnapshot:
        snapshot = self.get_snapshot(user_id, property_id)
        photo = self.repository.get_photo(user_id, property_id, media_id)
        if photo is not None:
            (self.settings.storage_dir / photo.relative_path).unlink(missing_ok=True)
            self.repository.delete_photo(user_id, property_id, media_id)
            return self.reorder_media(user_id, property_id, MediaOrderRequest())

        voice_note = self.repository.get_voice_note(user_id, property_id, media_id)
        if voice_note is None:
            raise NotFoundError("Медиафайл не найден.")
        (self.settings.storage_dir / voice_note.relative_path).unlink(missing_ok=True)
        self.repository.delete_voice_note(user_id, property_id, media_id)
        return self.get_snapshot(user_id, property_id)

    def reanalyze_photos(self, user_id: str, property_id: str) -> PropertySnapshot:
        snapshot = self.get_snapshot(user_id, property_id)
        duplicate_counts: dict[str, int] = {}
        for photo in snapshot.photos:
            duplicate_counts[photo.content_hash] = duplicate_counts.get(photo.content_hash, 0) + 1

        rescored: list[PhotoRecord] = []
        for index, photo in enumerate(sorted(snapshot.photos, key=lambda item: item.order_index)):
            path = self.settings.storage_dir / photo.relative_path
            quality_score, warnings, analysis = analyze_photo(
                path,
                photo.size_bytes,
                duplicate_counts.get(photo.content_hash, 0) > 1,
            )
            rescored.append(
                photo.model_copy(
                    update={
                        "order_index": index,
                        "quality_score": quality_score,
                        "warnings": warnings,
                        "analysis": analysis,
                    }
                )
            )

        if rescored:
            best_photo_id = max(rescored, key=lambda item: item.quality_score).id
            rescored = [photo.model_copy(update={"is_main": photo.id == best_photo_id}) for photo in rescored]
            self.repository.replace_photos(rescored)
        return self.get_snapshot(user_id, property_id)

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
        if not content and not transcript_override:
            raise ValidationError("Передайте аудиофайл или текст транскрипта.")
        self._validate_upload(filename, mime_type, content, allowed=self.settings.allowed_audio_formats_set(), allow_empty=bool(transcript_override))
        voice_dir = self.settings.storage_dir / user_id / property_id / "voice"
        voice_dir.mkdir(parents=True, exist_ok=True)
        extension = self._safe_extension(filename, fallback=".wav")
        safe_name = f"{uuid.uuid4().hex}{extension}"
        path = voice_dir / safe_name
        path.write_bytes(content)
        provider_name = self.settings.stt_provider.lower()
        error_text: str | None = None
        try:
            transcript = self.stt_provider.transcribe(path, transcript_override)
        except Exception as exc:
            error_text = str(exc)
            if self.settings.environment == "development":
                transcript = transcript_override or ""
                provider_name = "mock-fallback"
            else:
                raise ValidationError("Не удалось распознать аудио текущим STT-провайдером.") from exc
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
            provider=provider_name,
            error_text=error_text,
            created_at=datetime.now(UTC),
        )
        self.repository.save_voice_note(voice_note)
        snapshot.property = snapshot.property.model_copy(
            update={"transcript": transcript, "updated_at": datetime.now(UTC)}
        )
        self.repository.save_property(snapshot.property)
        snapshot.voice_notes = self.repository.list_voice_notes(user_id, property_id)
        return snapshot

    def list_evidence(self, user_id: str, property_id: str) -> list[PropertyFieldEvidence]:
        self.get_snapshot(user_id, property_id)
        return self.repository.list_evidence(user_id, property_id)

    def confirm_evidence(self, user_id: str, property_id: str, evidence_id: str) -> PropertySnapshot:
        snapshot = self.get_snapshot(user_id, property_id)
        evidence = self.repository.get_evidence(user_id, property_id, evidence_id)
        if evidence is None:
            raise NotFoundError("AI-подсказка не найдена.")
        apply_field_value(snapshot.property, evidence.field_name, evidence.value)
        snapshot.property = snapshot.property.model_copy(update={"updated_at": datetime.now(UTC)})
        self.repository.save_property(snapshot.property)
        self.repository.save_evidence(
            evidence.model_copy(update={"is_confirmed": True, "is_rejected": False, "updated_at": datetime.now(UTC)})
        )
        return self.get_snapshot(user_id, property_id)

    def reject_evidence(self, user_id: str, property_id: str, evidence_id: str) -> PropertySnapshot:
        self.get_snapshot(user_id, property_id)
        evidence = self.repository.get_evidence(user_id, property_id, evidence_id)
        if evidence is None:
            raise NotFoundError("AI-подсказка не найдена.")
        self.repository.save_evidence(
            evidence.model_copy(update={"is_confirmed": False, "is_rejected": True, "updated_at": datetime.now(UTC)})
        )
        return self.get_snapshot(user_id, property_id)

    def extract_fields(self, user_id: str, property_id: str) -> ExtractionResponse:
        snapshot = self.get_snapshot(user_id, property_id)
        if not snapshot.property.transcript:
            raise ValidationError("Сначала загрузите голосовую заметку или текст транскрипта.")
        fields = self.ai_provider.extract_fields(snapshot)
        self.repository.reject_pending_evidence(user_id, property_id, list(fields))
        evidence_items = self._save_ai_evidence(user_id, property_id, fields)
        self.repository.save_generation(
            GenerationRecord(
                id=str(uuid.uuid4()),
                property_id=property_id,
                user_id=user_id,
                generation_type="field_extraction",
                input_hash=sha1_text(snapshot.property.transcript or ""),
                output_json={key: value.model_dump(mode="json") for key, value in fields.items()},
                created_at=datetime.now(UTC),
            )
        )
        return ExtractionResponse(fields=fields, property=snapshot.property, evidence=evidence_items)

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
                "updated_at": datetime.now(UTC),
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
                created_at=datetime.now(UTC),
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
                    "updated_at": datetime.now(UTC),
                }
            )
            self.repository.save_property(snapshot.property)

        validation_results = {result.platform: result for result in self.validate_property(user_id, property_id, platforms)}
        export_id = str(uuid.uuid4())
        export_url = self.public_export_url(export_id)
        zip_path = self.settings.exports_dir / f"{export_id}.zip"
        jobs: list[PublicationJobRecord] = []
        archive_entries: list[str] = []

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
                    status="needs_review",
                    export_url=export_url,
                    prepared_payload={},
                    errors=validation.errors,
                    notes=validation.warnings,
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
                self.repository.save_publication_job(job)
                jobs.append(job)
                continue

            copy = generated.platform_copy.get(platform.value)
            if copy is None:
                raise ValidationError(f"Не удалось собрать платформенный текст для {platform.value}.")
            payload_dict = adapter.build_payload(snapshot, copy, validation)
            payload_dict["highlights"] = copy.highlights
            payload_dict["create_url"] = self.settings.platform_create_url(platform.value)
            payload = PlatformPayloadRecord(
                id=str(uuid.uuid4()),
                property_id=property_id,
                user_id=user_id,
                platform=platform,
                title=copy.title,
                description=copy.description,
                validation=validation,
                payload=payload_dict,
                created_at=datetime.now(UTC),
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
                    created_at=datetime.now(UTC),
                )
            )
            status = "waiting_for_platform" if payload.validation.channel.value == "url_feed" else "needs_action"
            job = PublicationJobRecord(
                id=str(uuid.uuid4()),
                property_id=property_id,
                user_id=user_id,
                platform=platform,
                channel=payload.validation.channel,
                status=status,
                feed_url=feed_url,
                export_url=export_url,
                prepared_payload=payload.payload,
                notes=payload.validation.warnings,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
                last_attempt_at=datetime.now(UTC),
            )
            self.repository.save_publication_job(job)
            jobs.append(job)

        if successful_payloads:
            snapshot.property = snapshot.property.model_copy(
                update={"status": PropertyStatus.ready, "updated_at": datetime.now(UTC)}
            )
            self.repository.save_property(snapshot.property)

        if zip_path.exists():
            with zipfile.ZipFile(zip_path) as archive:
                archive_entries = archive.namelist()
        self._write_export_manifest(
            ExportRecord(
                id=export_id,
                property_id=property_id,
                user_id=user_id,
                download_url=export_url,
                path=str(zip_path),
                entries=archive_entries,
                created_at=datetime.now(UTC),
            )
        )

        return PublishResponse(
            property=snapshot.property,
            validations=list(validation_results.values()),
            jobs=sorted(jobs, key=lambda item: item.created_at, reverse=True),
            export_url=export_url,
        )

    def list_publication_jobs(self, user_id: str, property_id: str) -> list[PublicationJobRecord]:
        return self.repository.list_publication_jobs(user_id, property_id)

    def get_publication_job(self, user_id: str, publication_id: str) -> PublicationJobRecord:
        job = self.repository.get_publication_job(user_id, publication_id)
        if job is None:
            raise NotFoundError("Публикация не найдена.")
        return job

    def mark_publication_published(
        self,
        user_id: str,
        publication_id: str,
        payload: PublicationStatusUpdateRequest,
    ) -> PublicationJobRecord:
        job = self.get_publication_job(user_id, publication_id)
        updated = job.model_copy(
            update={
                "status": "published",
                "external_id": payload.external_id or job.external_id,
                "external_url": payload.external_url or job.external_url,
                "published_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
                "notes": [*job.notes, payload.note] if payload.note else job.notes,
            }
        )
        self.repository.save_publication_job(updated)
        snapshot = self.get_snapshot(user_id, job.property_id)
        snapshot.property = snapshot.property.model_copy(update={"status": PropertyStatus.published, "updated_at": datetime.now(UTC)})
        self.repository.save_property(snapshot.property)
        return updated

    def deactivate_publication(self, user_id: str, publication_id: str, payload: PublicationStatusUpdateRequest) -> PublicationJobRecord:
        job = self.get_publication_job(user_id, publication_id)
        updated = job.model_copy(
            update={
                "status": "deactivated",
                "updated_at": datetime.now(UTC),
                "notes": [*job.notes, payload.note] if payload.note else job.notes,
            }
        )
        self.repository.save_publication_job(updated)
        return updated

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

    def get_export_record(self, export_id: str) -> ExportRecord:
        manifest_path = self.settings.exports_dir / f"{export_id}.json"
        if not manifest_path.exists():
            raise NotFoundError("Метаданные экспорта не найдены.")
        return ExportRecord.model_validate_json(manifest_path.read_text(encoding="utf-8"))

    def _save_ai_evidence(
        self,
        user_id: str,
        property_id: str,
        fields: dict[str, Any],
    ) -> list[PropertyFieldEvidence]:
        created: list[PropertyFieldEvidence] = []
        now = datetime.now(UTC)
        for field_name, field_value in fields.items():
            evidence = PropertyFieldEvidence(
                id=str(uuid.uuid4()),
                property_id=property_id,
                user_id=user_id,
                field_name=field_name,
                value=field_value.value,
                source=EvidenceSource.ai,
                confidence=field_value.confidence,
                source_quote=field_value.source_quote,
                is_confirmed=False,
                is_rejected=False,
                created_at=now,
                updated_at=now,
            )
            self.repository.save_evidence(evidence)
            created.append(evidence)
        return created

    def _write_export_manifest(self, export_record: ExportRecord) -> None:
        manifest_path = self.settings.exports_dir / f"{export_record.id}.json"
        manifest_path.write_text(export_record.model_dump_json(indent=2), encoding="utf-8")

    def _safe_extension(self, filename: str, fallback: str) -> str:
        extension = Path(filename).suffix.lower().strip()
        if not extension:
            return fallback
        return extension if extension.startswith(".") else f".{extension}"

    def _validate_upload(
        self,
        filename: str,
        mime_type: str | None,
        content: bytes,
        *,
        allowed: set[str],
        allow_empty: bool = False,
    ) -> None:
        if not content and not allow_empty:
            raise ValidationError("Файл пустой.")
        if len(content) > self.settings.upload_limit_bytes():
            raise ValidationError(
                f"Файл превышает лимит {self.settings.max_upload_size_mb} МБ для локального MVP."
            )
        extension = self._safe_extension(filename, fallback="").lstrip(".")
        if extension not in allowed:
            raise ValidationError(
                f"Формат файла .{extension or 'unknown'} не поддерживается. Разрешены: {', '.join(sorted(allowed))}."
            )
        guessed_mime, _ = mimetypes.guess_type(filename)
        effective_mime = (mime_type or guessed_mime or "").lower()
        if allowed == self.settings.allowed_image_formats_set() and effective_mime and not effective_mime.startswith("image/"):
            raise ValidationError("Ожидался файл изображения.")
        if allowed == self.settings.allowed_audio_formats_set() and effective_mime and not (
            effective_mime.startswith("audio/") or effective_mime == "text/plain"
        ):
            raise ValidationError("Ожидался аудиофайл или текстовый mock-транскрипт.")

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


def analyze_photo(path: Path, size_bytes: int, is_duplicate: bool) -> tuple[float, list[str], dict[str, Any]]:
    score = 0.45
    warnings: list[str] = []
    analysis: dict[str, Any] = {
        "duplicate": is_duplicate,
        "size_bytes": size_bytes,
        "brightness": None,
        "width": None,
        "height": None,
        "blur_score": None,
        "resolution_warning": False,
        "dark_warning": False,
        "overexposed_warning": False,
    }

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
                analysis["width"] = width
                analysis["height"] = height
                if min(width, height) < 600:
                    warnings.append("Низкое разрешение: площадки могут отдать фото в конец галереи.")
                    score -= 0.08
                    analysis["resolution_warning"] = True
                else:
                    score += 0.12
                grayscale = image.convert("L")
                blur_probe = grayscale.resize((64, 64))
                pixels = list(blur_probe.getdata())
                blur_score = 0.0
                if len(pixels) > 1:
                    diffs = [abs(int(pixels[index]) - int(pixels[index - 1])) for index in range(1, len(pixels))]
                    blur_score = sum(diffs) / len(diffs)
                    analysis["blur_score"] = round(blur_score, 2)
                    if blur_score < 8:
                        warnings.append("Фото выглядит размытым или слишком мягким.")
                        score -= 0.08
                    else:
                        score += 0.05
                if ImageStat is not None:
                    brightness = ImageStat.Stat(grayscale).mean[0]
                    analysis["brightness"] = round(brightness, 2)
                    if brightness < 45:
                        warnings.append("Фото выглядит слишком темным.")
                        score -= 0.08
                        analysis["dark_warning"] = True
                    elif brightness > 225:
                        warnings.append("Фото выглядит пересвеченным.")
                        score -= 0.04
                        analysis["overexposed_warning"] = True
                    else:
                        score += 0.1
        except Exception:
            warnings.append("Не удалось проанализировать изображение полностью, использована базовая эвристика.")
    else:
        warnings.append("Pillow не установлен: оценка фото выполнена по упрощенным правилам.")

    return max(0.0, min(score, 0.99)), warnings, analysis


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
    settings.ensure_directories()
    repository = Repository(settings.database_path)
    repository.init()
    return repository


def flatten_patch_fields(payload: dict[str, Any]) -> list[str]:
    touched: list[str] = []
    for key, value in payload.items():
        if value is None:
            continue
        if key in {"address", "features", "contacts"} and isinstance(value, dict):
            touched.extend([nested_key for nested_key, nested_value in value.items() if nested_value is not None])
        else:
            touched.append(key)
    return touched


def apply_field_value(property_record: PropertyRecord, field_name: str, value: Any) -> None:
    if hasattr(property_record.address, field_name):
        setattr(property_record.address, field_name, value)
        return
    if hasattr(property_record.features, field_name):
        setattr(property_record.features, field_name, value)
        return
    if hasattr(property_record.contacts, field_name):
        setattr(property_record.contacts, field_name, value)
        return
    if hasattr(property_record, field_name):
        setattr(property_record, field_name, value)


def build_archive_manifest_entries(zip_path: Path) -> list[str]:
    if not zip_path.exists():
        return []
    with zipfile.ZipFile(zip_path) as archive:
        return archive.namelist()
