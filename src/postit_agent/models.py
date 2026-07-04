from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, EmailStr, Field


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
    created_at: datetime


class PropertySnapshot(BaseModel):
    property: PropertyRecord
    profile: Profile
    photos: list[PhotoRecord] = Field(default_factory=list)
    voice_notes: list[VoiceNoteRecord] = Field(default_factory=list)


class AIFieldValue(BaseModel):
    value: Any = None
    confidence: float | None = None


class ExtractionResponse(BaseModel):
    fields: dict[str, AIFieldValue]
    property: PropertyRecord


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
    feed_url: str | None = None
    export_url: str | None = None
    errors: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    created_at: datetime


class PublishRequest(BaseModel):
    platforms: list[Platform] = Field(default_factory=lambda: list(Platform))


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


class GenerationRecord(BaseModel):
    id: str
    property_id: str
    user_id: str
    generation_type: str
    input_hash: str
    output_json: dict[str, Any]
    created_at: datetime

