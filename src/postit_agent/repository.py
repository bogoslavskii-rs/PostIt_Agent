from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from .models import FeedFileRecord, GenerationRecord, PhotoRecord, Platform, PlatformPayloadRecord, Profile, PropertyRecord, PublicationJobRecord, UserResponse, VoiceNoteRecord


ModelT = TypeVar("ModelT", bound=BaseModel)


class Repository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def init(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    phone TEXT,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS profiles (
                    user_id TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS properties (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    data TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_properties_user_id ON properties(user_id);

                CREATE TABLE IF NOT EXISTS photos (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    property_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    data TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_photos_property_id ON photos(property_id);

                CREATE TABLE IF NOT EXISTS voice_notes (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    property_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    data TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_voice_notes_property_id ON voice_notes(property_id);

                CREATE TABLE IF NOT EXISTS ai_generations (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    property_id TEXT NOT NULL,
                    generation_type TEXT NOT NULL,
                    input_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    data TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ai_generations_property_id ON ai_generations(property_id);

                CREATE TABLE IF NOT EXISTS platform_payloads (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    property_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    data TEXT NOT NULL,
                    UNIQUE(property_id, platform)
                );
                CREATE INDEX IF NOT EXISTS idx_platform_payloads_user_platform ON platform_payloads(user_id, platform);

                CREATE TABLE IF NOT EXISTS feed_files (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    data TEXT NOT NULL,
                    UNIQUE(user_id, platform)
                );

                CREATE TABLE IF NOT EXISTS publication_jobs (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    property_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    data TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_publication_jobs_property_id ON publication_jobs(property_id);
                """
            )

    @staticmethod
    def _dump(model: BaseModel) -> str:
        return json.dumps(model.model_dump(mode="json"), ensure_ascii=False)

    @staticmethod
    def _load(payload: str, model_type: type[ModelT]) -> ModelT:
        return model_type.model_validate(json.loads(payload))

    @staticmethod
    def _utcnow() -> str:
        return datetime.utcnow().isoformat()

    def create_user(self, user: UserResponse, password_hash: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO users (id, email, phone, password_hash, created_at) VALUES (?, ?, ?, ?, ?)",
                (user.id, user.email, user.phone, password_hash, user.created_at.isoformat()),
            )

    def get_user_by_email(self, email: str) -> tuple[UserResponse, str] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id, email, phone, password_hash, created_at FROM users WHERE lower(email) = lower(?)",
                (email,),
            ).fetchone()
        if row is None:
            return None
        user = UserResponse(
            id=row["id"],
            email=row["email"],
            phone=row["phone"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )
        return user, row["password_hash"]

    def get_user_by_id(self, user_id: str) -> UserResponse | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id, email, phone, created_at FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
        if row is None:
            return None
        return UserResponse(
            id=row["id"],
            email=row["email"],
            phone=row["phone"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def save_profile(self, user_id: str, profile: Profile) -> None:
        now = self._utcnow()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO profiles (user_id, data, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at
                """,
                (user_id, self._dump(profile), now),
            )

    def get_profile(self, user_id: str) -> Profile:
        with self.connect() as connection:
            row = connection.execute("SELECT data FROM profiles WHERE user_id = ?", (user_id,)).fetchone()
        if row is None:
            profile = Profile()
            self.save_profile(user_id, profile)
            return profile
        return self._load(row["data"], Profile)

    def save_property(self, property_record: PropertyRecord) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO properties (id, user_id, created_at, updated_at, data)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at
                """,
                (
                    property_record.id,
                    property_record.user_id,
                    property_record.created_at.isoformat(),
                    property_record.updated_at.isoformat(),
                    self._dump(property_record),
                ),
            )

    def get_property(self, user_id: str, property_id: str) -> PropertyRecord | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT data FROM properties WHERE id = ? AND user_id = ?",
                (property_id, user_id),
            ).fetchone()
        if row is None:
            return None
        return self._load(row["data"], PropertyRecord)

    def list_properties(self, user_id: str) -> list[PropertyRecord]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT data FROM properties WHERE user_id = ? ORDER BY updated_at DESC",
                (user_id,),
            ).fetchall()
        return [self._load(row["data"], PropertyRecord) for row in rows]

    def save_photo(self, photo: PhotoRecord) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO photos (id, user_id, property_id, created_at, data)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET data = excluded.data
                """,
                (photo.id, photo.user_id, photo.property_id, photo.created_at.isoformat(), self._dump(photo)),
            )

    def list_photos(self, user_id: str, property_id: str) -> list[PhotoRecord]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT data FROM photos WHERE property_id = ? AND user_id = ? ORDER BY created_at ASC",
                (property_id, user_id),
            ).fetchall()
        return [self._load(row["data"], PhotoRecord) for row in rows]

    def replace_photos(self, photos: list[PhotoRecord]) -> None:
        with self.connect() as connection:
            for photo in photos:
                connection.execute(
                    "UPDATE photos SET data = ? WHERE id = ?",
                    (self._dump(photo), photo.id),
                )

    def save_voice_note(self, voice_note: VoiceNoteRecord) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO voice_notes (id, user_id, property_id, created_at, data)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET data = excluded.data
                """,
                (
                    voice_note.id,
                    voice_note.user_id,
                    voice_note.property_id,
                    voice_note.created_at.isoformat(),
                    self._dump(voice_note),
                ),
            )

    def list_voice_notes(self, user_id: str, property_id: str) -> list[VoiceNoteRecord]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT data FROM voice_notes WHERE property_id = ? AND user_id = ? ORDER BY created_at DESC",
                (property_id, user_id),
            ).fetchall()
        return [self._load(row["data"], VoiceNoteRecord) for row in rows]

    def save_generation(self, generation: GenerationRecord) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO ai_generations (id, user_id, property_id, generation_type, input_hash, created_at, data)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET data = excluded.data
                """,
                (
                    generation.id,
                    generation.user_id,
                    generation.property_id,
                    generation.generation_type,
                    generation.input_hash,
                    generation.created_at.isoformat(),
                    self._dump(generation),
                ),
            )

    def save_platform_payload(self, payload: PlatformPayloadRecord) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO platform_payloads (id, user_id, property_id, platform, created_at, data)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(property_id, platform) DO UPDATE SET data = excluded.data, created_at = excluded.created_at
                """,
                (
                    payload.id,
                    payload.user_id,
                    payload.property_id,
                    payload.platform.value,
                    payload.created_at.isoformat(),
                    self._dump(payload),
                ),
            )

    def get_platform_payload(self, user_id: str, property_id: str, platform: Platform) -> PlatformPayloadRecord | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT data FROM platform_payloads WHERE property_id = ? AND user_id = ? AND platform = ?",
                (property_id, user_id, platform.value),
            ).fetchone()
        if row is None:
            return None
        return self._load(row["data"], PlatformPayloadRecord)

    def list_platform_payloads(self, user_id: str, platform: Platform) -> list[PlatformPayloadRecord]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT data FROM platform_payloads WHERE user_id = ? AND platform = ? ORDER BY created_at DESC",
                (user_id, platform.value),
            ).fetchall()
        return [self._load(row["data"], PlatformPayloadRecord) for row in rows]

    def save_feed_file(self, feed_file: FeedFileRecord) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO feed_files (id, user_id, platform, created_at, data)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, platform) DO UPDATE SET data = excluded.data, created_at = excluded.created_at
                """,
                (
                    feed_file.id,
                    feed_file.user_id,
                    feed_file.platform.value,
                    feed_file.created_at.isoformat(),
                    self._dump(feed_file),
                ),
            )

    def get_feed_file(self, user_id: str, platform: Platform) -> FeedFileRecord | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT data FROM feed_files WHERE user_id = ? AND platform = ?",
                (user_id, platform.value),
            ).fetchone()
        if row is None:
            return None
        return self._load(row["data"], FeedFileRecord)

    def save_publication_job(self, job: PublicationJobRecord) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO publication_jobs (id, user_id, property_id, platform, created_at, data)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET data = excluded.data
                """,
                (
                    job.id,
                    job.user_id,
                    job.property_id,
                    job.platform.value,
                    job.created_at.isoformat(),
                    self._dump(job),
                ),
            )

    def list_publication_jobs(self, user_id: str, property_id: str) -> list[PublicationJobRecord]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT data FROM publication_jobs WHERE property_id = ? AND user_id = ? ORDER BY created_at DESC",
                (property_id, user_id),
            ).fetchall()
        return [self._load(row["data"], PublicationJobRecord) for row in rows]
