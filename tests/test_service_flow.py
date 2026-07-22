from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from postit_agent.config import Settings
from postit_agent.models import Address, MediaOrderItem, MediaOrderRequest, Platform, PropertyCreate, PropertyFeatures, UserRegister
from postit_agent.services import ApplicationService, bootstrap_repository


def build_service(tmp_path: Path) -> ApplicationService:
    settings = Settings(
        database_path=tmp_path / "postit.sqlite3",
        storage_dir=tmp_path / "storage",
        exports_dir=tmp_path / "exports",
        feeds_dir=tmp_path / "feeds",
        static_dir=Path("src/postit_agent/static").resolve(),
        public_base_url="http://localhost:8000",
    )
    repository = bootstrap_repository(settings)
    return ApplicationService(settings, repository)


def image_bytes(color: tuple[int, int, int]) -> bytes:
    buffer = io.BytesIO()
    image = Image.new("RGB", (1200, 900), color=color)
    image.save(buffer, format="JPEG", quality=92)
    return buffer.getvalue()


def create_ready_property(service: ApplicationService, user_id: str):
    return service.create_property(
        user_id,
        PropertyCreate(
            confirmed=True,
            address=Address(city="Москва", street="Лесная", house="10"),
            features=PropertyFeatures(),
        ),
    )


def test_mock_vertical_slice_generates_feeds_and_export(tmp_path: Path):
    service = build_service(tmp_path)
    response = service.register(UserRegister(email="agent@example.com", password="supersecret", phone="+79990000000"))
    user_id = response.user.id
    property_record = create_ready_property(service, user_id)

    service.upload_photos(
        user_id,
        property_record.id,
        [
            (f"photo-{index}.jpg", "image/jpeg", image_bytes((120 + index * 10, 140, 180)))
            for index in range(5)
        ],
    )
    service.upload_voice_note(
        user_id,
        property_record.id,
        "voice.txt",
        "text/plain",
        b"voice transcript",
        "Двушка, 54 квадрата, 5 этаж из 17, кухня 10, монолит, хороший ремонт, окна во двор, цена 12 миллионов 500.",
    )

    extraction = service.extract_fields(user_id, property_record.id)
    assert extraction.evidence
    for evidence in extraction.evidence:
        service.confirm_evidence(user_id, property_record.id, evidence.id)

    copy = service.generate_copy(user_id, property_record.id, list(Platform))
    assert copy.description_full

    validations = service.validate_property(user_id, property_record.id, list(Platform))
    assert all(item.ready for item in validations)

    publication = service.publish_property(user_id, property_record.id, list(Platform))
    assert publication.export_url
    assert publication.jobs
    assert {job.status for job in publication.jobs} <= {"waiting_for_platform", "needs_action"}
    assert publication.property.status.value == "ready"

    export_id = publication.export_url.rsplit("/", 1)[-1].replace(".zip", "")
    export_record = service.get_export_record(export_id)
    assert "property.json" in export_record.entries
    assert "listing_copy.docx" in export_record.entries
    assert "html/avito.html" in export_record.entries

    assert service.get_feed_path(user_id, Platform.cian).exists()
    assert service.get_feed_path(user_id, Platform.yandex_realty).exists()


def test_pending_evidence_blocks_validation_until_resolved(tmp_path: Path):
    service = build_service(tmp_path)
    response = service.register(UserRegister(email="agent2@example.com", password="supersecret", phone="+79990000001"))
    user_id = response.user.id
    property_record = service.create_property(
        user_id,
        PropertyCreate(
            confirmed=True,
            price=12_500_000,
            address=Address(city="Москва", street="Лесная", house="10"),
            features=PropertyFeatures(
                rooms=2,
                area_total=54,
                area_kitchen=10,
                floor=5,
                floors_total=17,
                building_type="monolith",
                renovation="good",
            ),
        ),
    )
    service.upload_photos(
        user_id,
        property_record.id,
        [(f"photo-{index}.jpg", "image/jpeg", image_bytes((120, 160 + index * 5, 180))) for index in range(5)],
    )
    service.upload_voice_note(
        user_id,
        property_record.id,
        "voice.txt",
        "text/plain",
        b"voice transcript",
        "цена 12 миллионов 500, двушка, 54 квадрата, 5 этаж из 17",
    )
    service.generate_copy(user_id, property_record.id, [Platform.avito])
    extraction = service.extract_fields(user_id, property_record.id)

    validation = service.validate_property(user_id, property_record.id, [Platform.avito])[0]
    assert validation.ready is False
    assert any("требует подтверждения" in error for error in validation.errors)

    for evidence in extraction.evidence:
        service.reject_evidence(user_id, property_record.id, evidence.id)

    validation_after_reject = service.validate_property(user_id, property_record.id, [Platform.avito])[0]
    assert validation_after_reject.ready is True


def test_media_order_and_cover_can_be_updated(tmp_path: Path):
    service = build_service(tmp_path)
    response = service.register(UserRegister(email="agent3@example.com", password="supersecret", phone="+79990000002"))
    user_id = response.user.id
    property_record = create_ready_property(service, user_id)
    snapshot = service.upload_photos(
        user_id,
        property_record.id,
        [
            ("first.jpg", "image/jpeg", image_bytes((220, 220, 220))),
            ("second.jpg", "image/jpeg", image_bytes((50, 60, 80))),
        ],
    )
    first_photo = snapshot.photos[0]
    second_photo = snapshot.photos[1]

    reordered = service.reorder_media(
        user_id,
        property_record.id,
        MediaOrderRequest(
            items=[
                MediaOrderItem(media_id=second_photo.id, order_index=0),
                MediaOrderItem(media_id=first_photo.id, order_index=1),
            ],
            cover_media_id=second_photo.id,
        ),
    )

    assert reordered.photos[0].id == second_photo.id
    assert reordered.photos[0].is_main is True
