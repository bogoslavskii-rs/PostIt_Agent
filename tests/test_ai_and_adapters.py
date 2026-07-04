from __future__ import annotations

from datetime import UTC, datetime

from postit_agent.adapters import CianAdapter, ListingWithPayload, YandexRealtyAdapter
from postit_agent.ai import MockAIProvider, build_copy_prompts, build_extraction_prompts, normalize_extraction_payload
from postit_agent.models import Address, ContactInfo, PhotoRecord, Platform, PlatformCopy, PlatformPayloadRecord, PropertyFeatures, PropertyRecord, PropertySnapshot, Profile


def build_snapshot(confirmed: bool = True, photo_count: int = 5) -> PropertySnapshot:
    property_record = PropertyRecord(
        id="property-1",
        user_id="user-1",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        confirmed=confirmed,
        price=12_500_000,
        title_base="2-комнатная квартира 54 м²",
        description_base="Аккуратная квартира с понятной планировкой и хорошим светом.",
        transcript=(
            "Двушка, 54 квадрата, 5 этаж из 17, кухня 10, монолит, "
            "хороший ремонт, окна во двор, цена 12 миллионов 500."
        ),
        address=Address(city="Москва", street="Лесная", house="10"),
        contacts=ContactInfo(contact_phone="+79990000000", contact_name="Ирина"),
        features=PropertyFeatures(
            rooms=2,
            area_total=54,
            area_kitchen=10,
            floor=5,
            floors_total=17,
            building_type="monolith",
            renovation="good",
            windows_view="yard",
        ),
    )
    photos = [
        PhotoRecord(
            id=f"photo-{index}",
            property_id="property-1",
            user_id="user-1",
            original_name=f"photo-{index}.jpg",
            mime_type="image/jpeg",
            size_bytes=250_000,
            relative_path=f"user-1/property-1/photos/photo-{index}.jpg",
            public_url=f"http://localhost:8000/media/user-1/property-1/photos/photo-{index}.jpg",
            content_hash=f"hash-{index}",
            order_index=index,
            is_main=index == 0,
            quality_score=0.8,
            warnings=[],
            created_at=datetime.now(UTC),
        )
        for index in range(photo_count)
    ]
    return PropertySnapshot(
        property=property_record,
        profile=Profile(name="Ирина", contact_phone="+79990000000", city="Москва"),
        photos=photos,
        voice_notes=[],
    )


def test_mock_ai_extracts_core_fields():
    provider = MockAIProvider()
    fields = provider.extract_fields(build_snapshot())

    assert fields["price"].value == 12_500_000
    assert fields["rooms"].value == 2
    assert fields["area_total"].value == 54
    assert fields["floor"].value == 5
    assert fields["floors_total"].value == 17


def test_cian_adapter_blocks_unconfirmed_objects():
    adapter = CianAdapter()
    result = adapter.validate(build_snapshot(confirmed=False, photo_count=3))

    assert result.platform == Platform.cian
    assert result.ready is False
    assert any("подтверждена" in item for item in result.errors)
    assert any("минимум 5 фотографий" in item for item in result.errors)


def test_yandex_feed_contains_realty_root_and_offer():
    adapter = YandexRealtyAdapter()
    snapshot = build_snapshot()
    validation = adapter.validate(snapshot)
    payload_dict = adapter.build_payload(
        snapshot,
        PlatformCopy(
            title="2-комнатная квартира 54 м² в Москве",
            description=snapshot.property.description_base or "",
            highlights=["54 м²", "5 этаж из 17", "Окна во двор"],
        ),
        validation,
    )

    feed = adapter.build_feed(
        [
            ListingWithPayload(
                snapshot=snapshot,
                payload=PlatformPayloadRecord(
                    id="payload-1",
                    property_id="property-1",
                    user_id="user-1",
                    platform=Platform.yandex_realty,
                    title="2-комнатная квартира 54 м² в Москве",
                    description=snapshot.property.description_base or "",
                    validation=validation,
                    payload={**payload_dict, "highlights": ["54 м²"]},
                    created_at=datetime.now(UTC),
                ),
            )
        ]
    )

    assert "<realty-feed" in feed
    assert "internal-id=\"property-1\"" in feed
    assert "<offer" in feed


def test_ollama_extraction_prompts_include_schema_and_transcript():
    snapshot = build_snapshot()
    system_prompt, user_prompt = build_extraction_prompts(snapshot)

    assert "Не выдумывай" in system_prompt or "Нельзя выдумывать" in system_prompt
    assert "rooms" in user_prompt
    assert "Транскрипт голосовой заметки" in user_prompt
    assert "12 миллионов 500" in user_prompt


def test_ollama_copy_prompts_include_platform_rules():
    snapshot = build_snapshot()
    system_prompt, user_prompt = build_copy_prompts(snapshot, [Platform.avito, Platform.cian])

    assert "senior-copywriter" in system_prompt
    assert "avito" in user_prompt
    assert "cian" in user_prompt
    assert "Не включай телефон" in user_prompt


def test_normalize_extraction_payload_coerces_types_and_enums():
    normalized = normalize_extraction_payload(
        {
            "rooms": {"value": "2", "confidence": "0.91"},
            "area_total": {"value": "54,5", "confidence": 0.87},
            "building_type": {"value": "Монолит", "confidence": 1},
            "price": {"value": "12 500 000 ₽", "confidence": "0.95"},
        }
    )

    assert normalized["rooms"].value == 2
    assert normalized["area_total"].value == 54.5
    assert normalized["building_type"].value == "monolith"
    assert normalized["price"].value == 12500000
