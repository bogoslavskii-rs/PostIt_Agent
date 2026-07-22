from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator


class Platform(str, Enum):
    avito = "avito"
    cian = "cian"
    yandex_realty = "yandex_realty"
    youla = "youla"
    domclick = "domclick"


class PublicationChannel(str, Enum):
    url_feed = "url_feed"
    manual_export = "manual_export"
    user_assisted = "user_assisted"


class PropertyStatus(str, Enum):
    draft = "draft"
    reviewed = "reviewed"
    ready = "ready"
    published = "published"


class PropertyType(str, Enum):
    apartment = "apartment"


class DealType(str, Enum):
    sale = "sale"


class Address(BaseModel):
    country: str = "Россия"
    region: str | None = None
    city: str | None = None
    district: str | None = None
    street: str | None = None
    house: str | None = None
    apartment: str | None = None
    postcode: str | None = None
    latitude: float | None = None
    longitude: float | None = None

    @field_validator("latitude")
    @classmethod
    def validate_latitude(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if value < -90 or value > 90:
            raise ValueError("Широта должна быть в диапазоне от -90 до 90.")
        return value

    @field_validator("longitude")
    @classmethod
    def validate_longitude(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if value < -180 or value > 180:
            raise ValueError("Долгота должна быть в диапазоне от -180 до 180.")
        return value

    def one_line(self) -> str:
        parts = [self.country, self.region, self.city, self.district, self.street, self.house, self.apartment]
        return ", ".join([part for part in parts if part])


class PropertyFeatures(BaseModel):
    rooms: int | None = None
    area_total: float | None = None
    area_living: float | None = None
    area_kitchen: float | None = None
    floor: int | None = None
    floors_total: int | None = None
    ceiling_height: float | None = None
    building_type: str | None = None
    renovation: str | None = None
    bathroom: str | None = None
    balcony: str | None = None
    windows_view: str | None = None
    parking: str | None = None
    year_built: int | None = None

    @field_validator("rooms", "floor", "floors_total")
    @classmethod
    def validate_non_negative_int(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if value < 0:
            raise ValueError("Числовое значение не может быть отрицательным.")
        return value

    @field_validator("area_total", "area_living", "area_kitchen", "ceiling_height")
    @classmethod
    def validate_positive_area(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if value <= 0:
            raise ValueError("Площадь и высота потолка должны быть больше нуля.")
        return value

    @field_validator("year_built")
    @classmethod
    def validate_year_built(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if value < 1800 or value > 2100:
            raise ValueError("Год постройки выглядит некорректным.")
        return value

    @model_validator(mode="after")
    def validate_feature_relations(self) -> "PropertyFeatures":
        if self.area_total is not None and self.area_living is not None and self.area_living > self.area_total:
            raise ValueError("Жилая площадь не может превышать общую.")
        if self.area_total is not None and self.area_kitchen is not None and self.area_kitchen > self.area_total:
            raise ValueError("Площадь кухни не может превышать общую.")
        if self.floor is not None and self.floors_total is not None and self.floor > self.floors_total:
            raise ValueError("Этаж не может быть выше этажности дома.")
        return self


class EvidenceSource(str, Enum):
    manual = "manual"
    voice = "voice"
    photo = "photo"
    ai = "ai"
    external = "external"


class ContactInfo(BaseModel):
    contact_name: str | None = None
    contact_phone: str | None = None
    contact_email: EmailStr | None = None


class PlatformAccountState(BaseModel):
    autoload_enabled: bool = False
    manual_export_allowed: bool = True
    note: str | None = None


class Profile(BaseModel):
    name: str | None = None
    agency_name: str | None = None
    city: str | None = None
    contact_phone: str | None = None
    contact_email: EmailStr | None = None
    legal_status: str | None = None
    bio: str | None = None
    platform_accounts: dict[str, PlatformAccountState] = Field(default_factory=dict)


class UserRegister(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    phone: str | None = None


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    id: str
    email: EmailStr
    phone: str | None = None
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class PropertyPayload(BaseModel):
    external_id: str | None = None
    type: PropertyType = PropertyType.apartment
    deal_type: DealType = DealType.sale
    status: PropertyStatus = PropertyStatus.draft
    address: Address = Field(default_factory=Address)
    features: PropertyFeatures = Field(default_factory=PropertyFeatures)
    contacts: ContactInfo = Field(default_factory=ContactInfo)
    price: int | None = None
    currency: str = "RUB"
    description_short: str | None = None
    description_base: str | None = None
    title_base: str | None = None
    title_variants: list[str] = Field(default_factory=list)
    highlights: list[str] = Field(default_factory=list)
    transcript: str | None = None
    notes: str | None = None
    confirmed: bool = False
    seller_type: str = "agent"

    @field_validator("price")
    @classmethod
    def validate_price(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if value <= 0:
            raise ValueError("Цена должна быть больше нуля.")
        return value


class PropertyCreate(PropertyPayload):
    pass


class PropertyUpdate(BaseModel):
    external_id: str | None = None
    status: PropertyStatus | None = None
    address: Address | None = None
    features: PropertyFeatures | None = None
    contacts: ContactInfo | None = None
    price: int | None = None
    currency: str | None = None
    description_short: str | None = None
    description_base: str | None = None
    title_base: str | None = None
    title_variants: list[str] | None = None
    highlights: list[str] | None = None
    transcript: str | None = None
    notes: str | None = None
    confirmed: bool | None = None
    seller_type: str | None = None


class PropertyRecord(PropertyPayload):
    id: str
    user_id: str
    created_at: datetime
    updated_at: datetime


class PhotoRecord(BaseModel):
    id: str
    property_id: str
    user_id: str
    original_name: str
    mime_type: str
    size_bytes: int
    relative_path: str
    public_url: str
    content_hash: str
    order_index: int
    is_main: bool = False
    quality_score: float = 0.0
    warnings: list[str] = Field(default_factory=list)
    analysis: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class VoiceNoteRecord(BaseModel):
    id: str
    property_id: str
    user_id: str
    original_name: str
    mime_type: str
    relative_path: str
    public_url: str
    transcript: str | None = None
    status: str = "uploaded"
    provider: str = "mock"
    error_text: str | None = None
    created_at: datetime


class PropertyFieldEvidence(BaseModel):
    id: str
    property_id: str
    user_id: str
    field_name: str
    value: Any = None
    source: EvidenceSource = EvidenceSource.ai
    confidence: float | None = None
    source_quote: str | None = None
    is_confirmed: bool = False
    is_rejected: bool = False
    created_at: datetime
    updated_at: datetime

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if value < 0 or value > 1:
            raise ValueError("Confidence должна быть в диапазоне от 0 до 1.")
        return value


class PropertySnapshot(BaseModel):
    property: PropertyRecord
    profile: Profile
    photos: list[PhotoRecord] = Field(default_factory=list)
    voice_notes: list[VoiceNoteRecord] = Field(default_factory=list)
    evidence: list[PropertyFieldEvidence] = Field(default_factory=list)


class AIFieldValue(BaseModel):
    value: Any = None
    confidence: float | None = None
    source_quote: str | None = None


class ExtractionResponse(BaseModel):
    fields: dict[str, AIFieldValue]
    property: PropertyRecord
    evidence: list[PropertyFieldEvidence] = Field(default_factory=list)


class PlatformCopy(BaseModel):
    title: str
    description: str
    highlights: list[str] = Field(default_factory=list)


class CopyGenerationRequest(BaseModel):
    platforms: list[Platform] = Field(default_factory=lambda: list(Platform))


class CopyGenerationResponse(BaseModel):
    titles: list[str]
    description_short: str
    description_full: str
    highlights: list[str]
    platform_copy: dict[str, PlatformCopy]
    property: PropertyRecord


class ValidationRequest(BaseModel):
    platforms: list[Platform] = Field(default_factory=lambda: list(Platform))


class MediaOrderItem(BaseModel):
    media_id: str
    order_index: int


class MediaOrderRequest(BaseModel):
    items: list[MediaOrderItem] = Field(default_factory=list)
    cover_media_id: str | None = None


class MediaPatchRequest(BaseModel):
    order_index: int | None = None
    is_main: bool | None = None


class PlatformValidationResult(BaseModel):
    platform: Platform
    ready: bool
    channel: PublicationChannel
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class PlatformPayloadRecord(BaseModel):
    id: str
    property_id: str
    user_id: str
    platform: Platform
    title: str
    description: str
    validation: PlatformValidationResult
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class PublicationJobRecord(BaseModel):
    id: str
    property_id: str
    user_id: str
    platform: Platform
    channel: PublicationChannel
    status: str
    external_id: str | None = None
    external_url: str | None = None
    feed_url: str | None = None
    export_url: str | None = None
    prepared_payload: dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    last_attempt_at: datetime | None = None
    published_at: datetime | None = None
    created_at: datetime
    updated_at: datetime | None = None


class PublishRequest(BaseModel):
    platforms: list[Platform] = Field(default_factory=lambda: list(Platform))


class PublicationStatusUpdateRequest(BaseModel):
    external_id: str | None = None
    external_url: str | None = None
    note: str | None = None


class PublishResponse(BaseModel):
    property: PropertyRecord
    validations: list[PlatformValidationResult]
    jobs: list[PublicationJobRecord]
    export_url: str | None = None


class FeedFileRecord(BaseModel):
    id: str
    user_id: str
    platform: Platform
    path: str
    feed_url: str
    checksum: str
    created_at: datetime


class ExportRecord(BaseModel):
    id: str
    property_id: str
    user_id: str
    download_url: str
    path: str
    entries: list[str] = Field(default_factory=list)
    created_at: datetime


class GenerationRecord(BaseModel):
    id: str
    property_id: str
    user_id: str
    generation_type: str
    input_hash: str
    output_json: dict[str, Any]
    created_at: datetime
