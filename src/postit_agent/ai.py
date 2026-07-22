from __future__ import annotations

import json
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Settings
from .models import AIFieldValue, Platform, PlatformCopy, PropertySnapshot


ADDRESS_KEYS = {"region", "city", "district", "street", "house", "apartment", "postcode", "latitude", "longitude"}
FEATURE_KEYS = {
    "rooms",
    "area_total",
    "area_living",
    "area_kitchen",
    "floor",
    "floors_total",
    "ceiling_height",
    "building_type",
    "renovation",
    "bathroom",
    "balcony",
    "windows_view",
    "parking",
    "year_built",
}

ENUM_NORMALIZERS = {
    "building_type": {
        "monolith": "monolith",
        "монолит": "monolith",
        "brick": "brick",
        "кирпич": "brick",
        "panel": "panel",
        "панель": "panel",
        "block": "block",
        "блок": "block",
        "wood": "wood",
        "дерево": "wood",
        "wooden": "wood",
    },
    "renovation": {
        "designer": "designer",
        "дизайнерский": "designer",
        "дизайнерский ремонт": "designer",
        "euro": "euro",
        "евро": "euro",
        "евроремонт": "euro",
        "good": "good",
        "хороший ремонт": "good",
        "good repair": "good",
        "cosmetic": "cosmetic",
        "косметический": "cosmetic",
        "needs_repair": "needs_repair",
        "без ремонта": "needs_repair",
        "под ремонт": "needs_repair",
    },
    "windows_view": {
        "yard": "yard",
        "во двор": "yard",
        "street": "street",
        "на улицу": "street",
        "mixed": "mixed",
        "на две стороны": "mixed",
    },
    "balcony": {
        "none": "none",
        "нет": "none",
        "balcony": "balcony",
        "балкон": "balcony",
        "loggia": "loggia",
        "лоджия": "loggia",
        "both": "both",
        "балкон и лоджия": "both",
    },
}

EXTRACTION_SCHEMA_KEYS = [
    "city",
    "street",
    "house",
    "rooms",
    "area_total",
    "area_living",
    "area_kitchen",
    "floor",
    "floors_total",
    "building_type",
    "renovation",
    "windows_view",
    "balcony",
    "bathroom",
    "parking",
    "price",
]

EXTRACTION_SCHEMA_DESCRIPTION = {
    "city": "string | null",
    "street": "string | null",
    "house": "string | null",
    "rooms": "integer | null, студия = 0",
    "area_total": "number | null",
    "area_living": "number | null",
    "area_kitchen": "number | null",
    "floor": "integer | null",
    "floors_total": "integer | null",
    "building_type": "one of [monolith, brick, panel, block, wood] or null",
    "renovation": "one of [designer, euro, good, cosmetic, needs_repair] or null",
    "windows_view": "one of [yard, street, mixed] or null",
    "balcony": "one of [none, balcony, loggia, both] or null",
    "bathroom": "string | null",
    "parking": "string | null",
    "price": "integer | null, always in RUB without spaces",
}

PLATFORM_COPY_RULES = {
    Platform.avito: "title <= 50 chars, description <= 2200 chars, no phone, no external links, no HTML",
    Platform.cian: "description 15-3000 chars, precise address wording, no broken symbols or HTML",
    Platform.yandex_realty: "clear factual style, no HTML except plain line breaks semantics, stable terminology",
    Platform.youla: "no contacts in title or description, concise, no spam wording, <= 30 photo context assumed",
    Platform.domclick: "neutral and factual copy, no promises of guaranteed approval or legal claims",
}


@dataclass
class GeneratedCopy:
    titles: list[str]
    description_short: str
    description_full: str
    highlights: list[str]
    platform_copy: dict[str, PlatformCopy]


class SpeechToTextProvider:
    def transcribe(self, file_path: Path, transcript_override: str | None = None) -> str:
        raise NotImplementedError


class MockSpeechToTextProvider(SpeechToTextProvider):
    def transcribe(self, file_path: Path, transcript_override: str | None = None) -> str:
        if transcript_override:
            return transcript_override.strip()
        sidecar = file_path.with_suffix(".txt")
        if sidecar.exists():
            return sidecar.read_text(encoding="utf-8").strip()
        return "Двушка, 54 квадрата, 5 этаж из 17, кухня 10, монолит, хороший ремонт, окна во двор, цена 12 миллионов 500."


class WhisperCppSpeechToTextProvider(SpeechToTextProvider):
    def __init__(self, binary: str, model_path: str) -> None:
        self.binary = binary
        self.model_path = model_path

    def transcribe(self, file_path: Path, transcript_override: str | None = None) -> str:
        if transcript_override:
            return transcript_override.strip()
        output_prefix = file_path.with_suffix("")
        command = [
            self.binary,
            "-m",
            self.model_path,
            "-f",
            str(file_path),
            "-otxt",
            "-of",
            str(output_prefix),
        ]
        subprocess.run(command, check=True, capture_output=True, text=True)
        txt_path = output_prefix.with_suffix(".txt")
        return txt_path.read_text(encoding="utf-8").strip()


class AIProvider:
    def extract_fields(self, snapshot: PropertySnapshot) -> dict[str, AIFieldValue]:
        raise NotImplementedError

    def generate_copy(self, snapshot: PropertySnapshot, platforms: list[Platform]) -> GeneratedCopy:
        raise NotImplementedError


class MockAIProvider(AIProvider):
    ROOM_WORDS = {
        "студ": 0,
        "одн": 1,
        "дв": 2,
        "тр": 3,
        "четыр": 4,
        "пят": 5,
    }

    def extract_fields(self, snapshot: PropertySnapshot) -> dict[str, AIFieldValue]:
        transcript = (snapshot.property.transcript or "").lower()
        fields: dict[str, AIFieldValue] = {}

        price = self._extract_price(transcript)
        if price is not None:
            fields["price"] = AIFieldValue(value=price, confidence=0.95, source_quote=self._quote(transcript, "цена"))

        rooms = self._extract_rooms(transcript)
        if rooms is not None:
            fields["rooms"] = AIFieldValue(value=rooms, confidence=0.93, source_quote=self._quote(transcript, "комн"))

        if match := re.search(r"(\d+(?:[.,]\d+)?)\s*(?:квадрат|кв(?:\.|\s*м)|метр)", transcript):
            fields["area_total"] = AIFieldValue(value=float(match.group(1).replace(",", ".")), confidence=0.92, source_quote=match.group(0))

        if match := re.search(r"кухн[яи]?\s*(\d+(?:[.,]\d+)?)", transcript):
            fields["area_kitchen"] = AIFieldValue(value=float(match.group(1).replace(",", ".")), confidence=0.88, source_quote=match.group(0))

        if match := re.search(r"(\d+)\s*этаж(?:а|е)?\s*из\s*(\d+)", transcript):
            fields["floor"] = AIFieldValue(value=int(match.group(1)), confidence=0.95, source_quote=match.group(0))
            fields["floors_total"] = AIFieldValue(value=int(match.group(2)), confidence=0.95, source_quote=match.group(0))

        for keyword, normalized in {
            "монолит": "monolith",
            "кирпич": "brick",
            "панель": "panel",
            "блок": "block",
        }.items():
            if keyword in transcript:
                fields["building_type"] = AIFieldValue(value=normalized, confidence=0.82, source_quote=keyword)
                break

        for keyword, normalized in {
            "дизайнер": "designer",
            "евро": "euro",
            "хороший ремонт": "good",
            "космет": "cosmetic",
            "без ремонта": "needs_repair",
        }.items():
            if keyword in transcript:
                fields["renovation"] = AIFieldValue(value=normalized, confidence=0.8, source_quote=keyword)
                break

        if "во двор" in transcript:
            fields["windows_view"] = AIFieldValue(value="yard", confidence=0.86, source_quote="во двор")
        elif "на улицу" in transcript:
            fields["windows_view"] = AIFieldValue(value="street", confidence=0.86, source_quote="на улицу")

        if "лодж" in transcript:
            fields["balcony"] = AIFieldValue(value="loggia", confidence=0.82, source_quote="лодж")
        elif "балкон" in transcript:
            fields["balcony"] = AIFieldValue(value="balcony", confidence=0.82, source_quote="балкон")

        if match := re.search(r"(москва|санкт-петербург|казань|сочи|екатеринбург|новосибирск|краснодар)", transcript):
            fields["city"] = AIFieldValue(value=match.group(1).title(), confidence=0.72, source_quote=match.group(0))

        if match := re.search(r"(?:улица|ул\.?)\s+([а-яa-z0-9\- ]+)", transcript):
            fields["street"] = AIFieldValue(value=match.group(1).strip().title(), confidence=0.66, source_quote=match.group(0))

        if match := re.search(r"(?:дом|д\.)\s*([0-9а-яa-z\-]+)", transcript):
            fields["house"] = AIFieldValue(value=match.group(1).strip(), confidence=0.7, source_quote=match.group(0))

        return fields

    def _quote(self, transcript: str, keyword: str) -> str | None:
        for chunk in re.split(r"[,.]", transcript):
            if keyword in chunk:
                compact = chunk.strip()
                return compact or None
        return None

    def generate_copy(self, snapshot: PropertySnapshot, platforms: list[Platform]) -> GeneratedCopy:
        property_record = snapshot.property
        address = property_record.address
        features = property_record.features
        city = address.city or snapshot.profile.city or "удачной локации"
        area = f"{features.area_total:g} м²" if features.area_total else "комфортной площади"
        rooms = self._rooms_label(features.rooms)
        floor = self._floor_label(features.floor, features.floors_total)
        price = f"{property_record.price:,}".replace(",", " ") + " ₽" if property_record.price else "по запросу"
        building = {
            "monolith": "монолитном доме",
            "brick": "кирпичном доме",
            "panel": "панельном доме",
        }.get(features.building_type or "", "доме")
        renovation = {
            "designer": "дизайнерским ремонтом",
            "euro": "евроремонтом",
            "good": "хорошим ремонтом",
            "cosmetic": "аккуратным косметическим ремонтом",
            "needs_repair": "в состоянии под обновление",
        }.get(features.renovation or "", "приятным состоянием")

        titles = [
            f"{rooms} {area} в {city}",
            f"{rooms.capitalize()} в {city} за {price}",
            f"{area} в {building} с {renovation}",
        ]

        highlights = [
            text
            for text in [
                f"{rooms.capitalize()} с продуманной планировкой" if rooms else None,
                f"Площадь {area}",
                floor,
                f"Цена {price}",
                "Окна во двор" if features.windows_view == "yard" else None,
                "Есть лоджия" if features.balcony == "loggia" else "Есть балкон" if features.balcony == "balcony" else None,
            ]
            if text
        ]

        description_short = (
            f"{rooms.capitalize()} в {city}: {area}, {floor.lower() if floor else 'удобный этаж'}, "
            f"{renovation}, цена {price}."
        )
        description_full = (
            f"Продается {rooms} в {city}. Объект расположен в {building} и подойдет покупателю, "
            f"которому важны быстрый выход на сделку и понятная, аккуратно собранная карточка объекта.\n\n"
            f"По параметрам: площадь {area}, {floor.lower() if floor else 'этаж уточняется'}, "
            f"состояние квартиры — с {renovation}. "
            f"{'Окна во двор, поэтому в квартире тише и спокойнее. ' if features.windows_view == 'yard' else ''}"
            f"{'Есть лоджия. ' if features.balcony == 'loggia' else 'Есть балкон. ' if features.balcony == 'balcony' else ''}"
            f"Цена предложения — {price}.\n\n"
            "Описание подготовлено без маркетингового мусора и готово к адаптации под площадки. "
            "Перед публикацией риелтор подтверждает все поля вручную."
        )

        platform_copy = {
            platform.value: self._adapt_copy_for_platform(platform, titles[0], description_full, highlights)
            for platform in platforms
        }

        return GeneratedCopy(
            titles=titles,
            description_short=description_short,
            description_full=description_full,
            highlights=highlights,
            platform_copy=platform_copy,
        )

    def _extract_price(self, transcript: str) -> int | None:
        if match := re.search(r"(\d[\d\s]{4,})\s*(?:₽|руб)", transcript):
            return int(re.sub(r"\s+", "", match.group(1)))

        if match := re.search(
            r"(\d+(?:[.,]\d+)?)\s*(?:млн|миллион(?:ов|а)?)\.?(?:\s+(\d{1,3}))?",
            transcript,
        ):
            millions = float(match.group(1).replace(",", "."))
            price = int(millions * 1_000_000)
            if match.group(2):
                price += int(match.group(2)) * 1_000
            return price

        if match := re.search(r"цена\s*(\d[\d\s]+)", transcript):
            return int(re.sub(r"\s+", "", match.group(1)))
        return None

    def _extract_rooms(self, transcript: str) -> int | None:
        if "студ" in transcript:
            return 0
        if match := re.search(r"(\d+)\s*(?:комнат|к(?:\.|\s)|-комн)", transcript):
            return int(match.group(1))
        for prefix, value in self.ROOM_WORDS.items():
            if prefix in transcript:
                return value
        return None

    def _rooms_label(self, rooms: int | None) -> str:
        if rooms is None:
            return "Квартира"
        if rooms == 0:
            return "студия"
        return f"{rooms}-комнатная квартира"

    def _floor_label(self, floor: int | None, floors_total: int | None) -> str | None:
        if floor is None or floors_total is None:
            return None
        return f"{floor} этаж из {floors_total}"

    def _adapt_copy_for_platform(
        self,
        platform: Platform,
        title: str,
        description: str,
        highlights: list[str],
    ) -> PlatformCopy:
        sanitized_description = sanitize_text(description)
        sanitized_title = sanitize_text(title)

        if platform == Platform.avito:
            sanitized_title = trimmed(sanitized_title, 50)
            sanitized_description = trimmed(sanitized_description, 2200)
        elif platform == Platform.cian:
            sanitized_description = trimmed(sanitized_description, 3000)
        elif platform in {Platform.yandex_realty, Platform.youla, Platform.domclick}:
            sanitized_description = sanitized_description.replace("телефон", "")

        return PlatformCopy(
            title=sanitized_title,
            description=sanitized_description,
            highlights=highlights[:5],
        )


class OllamaAIProvider(AIProvider):
    def __init__(self, settings: Settings, fallback: MockAIProvider) -> None:
        self.settings = settings
        self.fallback = fallback

    def extract_fields(self, snapshot: PropertySnapshot) -> dict[str, AIFieldValue]:
        transcript = snapshot.property.transcript or ""
        if not transcript.strip():
            return {}

        system_prompt, user_prompt = build_extraction_prompts(snapshot)
        try:
            payload = self._request_json(system_prompt, user_prompt)
        except Exception:
            return self.fallback.extract_fields(snapshot)

        normalized = normalize_extraction_payload(payload)
        return normalized or self.fallback.extract_fields(snapshot)

    def generate_copy(self, snapshot: PropertySnapshot, platforms: list[Platform]) -> GeneratedCopy:
        system_prompt, user_prompt = build_copy_prompts(snapshot, platforms)
        try:
            payload = self._request_json(system_prompt, user_prompt)
        except Exception:
            return self.fallback.generate_copy(snapshot, platforms)

        try:
            titles = [str(item) for item in payload.get("titles", [])][:3]
            description_short = str(payload.get("description_short", "")).strip()
            description_full = str(payload.get("description_full", "")).strip()
            highlights = [str(item) for item in payload.get("highlights", [])]
            raw_platform_copy = payload.get("platform_copy", {})
            platform_copy: dict[str, PlatformCopy] = {}
            for platform in platforms:
                copy_payload = raw_platform_copy.get(platform.value, {})
                platform_copy[platform.value] = PlatformCopy(
                    title=str(copy_payload.get("title", titles[0] if titles else "")),
                    description=str(copy_payload.get("description", description_full)),
                    highlights=[str(item) for item in copy_payload.get("highlights", highlights)],
                )

            if not titles or not description_full:
                raise ValueError("Incomplete Ollama response")

            normalized_platform_copy = {
                platform.value: self.fallback._adapt_copy_for_platform(
                    platform,
                    platform_copy[platform.value].title,
                    platform_copy[platform.value].description,
                    platform_copy[platform.value].highlights,
                )
                for platform in platforms
            }
            return GeneratedCopy(
                titles=titles,
                description_short=description_short or description_full[:200],
                description_full=description_full,
                highlights=highlights,
                platform_copy=normalized_platform_copy,
            )
        except Exception:
            return self.fallback.generate_copy(snapshot, platforms)

    def _request_json(self, system_prompt: str, prompt: str) -> dict[str, Any]:
        body = json.dumps(
            {
                "model": self.settings.ollama_model,
                "system": system_prompt,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "options": {
                    "temperature": self.settings.ollama_temperature,
                    "top_p": self.settings.ollama_top_p,
                    "repeat_penalty": self.settings.ollama_repeat_penalty,
                    "num_ctx": self.settings.ollama_num_ctx,
                    "num_predict": self.settings.ollama_num_predict,
                },
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.settings.ollama_base_url.rstrip('/')}/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.settings.ollama_timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        raw_response = payload.get("response", "{}")
        return json.loads(extract_json_block(raw_response))


def extract_json_block(text: str) -> str:
    text = text.strip()
    if text.startswith("{") and text.endswith("}"):
        return text
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or start >= end:
        raise ValueError("No JSON block found in model response")
    return text[start : end + 1]


def sanitize_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\+?\d[\d\-\(\)\s]{8,}\d", "", text)
    text = text.replace("&", "и")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def trimmed(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def apply_extracted_fields(snapshot: PropertySnapshot, fields: dict[str, AIFieldValue]) -> PropertySnapshot:
    property_record = snapshot.property
    address_updates = property_record.address.model_dump()
    features_updates = property_record.features.model_dump()
    contacts_updates = property_record.contacts.model_dump()
    scalar_updates: dict[str, Any] = {}

    for key, field_value in fields.items():
        if field_value.value is None:
            continue
        if key in ADDRESS_KEYS:
            address_updates[key] = field_value.value
        elif key in FEATURE_KEYS:
            features_updates[key] = field_value.value
        elif key.startswith("contact_"):
            contacts_updates[key] = field_value.value
        else:
            scalar_updates[key] = field_value.value

    snapshot.property = property_record.model_copy(
        update={
            **scalar_updates,
            "address": property_record.address.model_copy(update=address_updates),
            "features": property_record.features.model_copy(update=features_updates),
            "contacts": property_record.contacts.model_copy(update=contacts_updates),
        }
    )
    return snapshot


def build_extraction_prompts(snapshot: PropertySnapshot) -> tuple[str, str]:
    system_prompt = (
        "Ты data-extractor для российского рынка недвижимости. "
        "Твоя задача: из голосовой заметки риелтора достать только факты и вернуть только JSON. "
        "Нельзя выдумывать значения. Если факта нет или ты не уверен, верни null. "
        "Нормализуй значения в канонический вид для CRM и feed adapters.\n"
        "Правила нормализации:\n"
        "- rooms: студия = 0.\n"
        "- price: целое число в рублях без пробелов и валютных символов.\n"
        "- building_type только из [monolith, brick, panel, block, wood].\n"
        "- renovation только из [designer, euro, good, cosmetic, needs_repair].\n"
        "- windows_view только из [yard, street, mixed].\n"
        "- balcony только из [none, balcony, loggia, both].\n"
        "- confidence: число от 0 до 1.\n"
            "Ответ должен содержать все ключи схемы, даже если часть значений null."
    )
    existing = snapshot.property.model_dump(mode="json")
    user_prompt = (
        "Схема результата:\n"
        + json.dumps(
            {
                key: {
                    "value": EXTRACTION_SCHEMA_DESCRIPTION[key],
                    "confidence": "0..1",
                    "source_quote": "fragment from transcript or null",
                }
                for key in EXTRACTION_SCHEMA_KEYS
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n\n"
        + "Пример:\n"
        + json.dumps(
            {
                "rooms": {"value": 2, "confidence": 0.98, "source_quote": "двушка"},
                "area_total": {"value": 54, "confidence": 0.95, "source_quote": "54 квадрата"},
                "floor": {"value": 5, "confidence": 0.95, "source_quote": "5 этаж из 17"},
                "floors_total": {"value": 17, "confidence": 0.95, "source_quote": "5 этаж из 17"},
                "area_kitchen": {"value": 10, "confidence": 0.9, "source_quote": "кухня 10"},
                "building_type": {"value": "monolith", "confidence": 0.8, "source_quote": "монолит"},
                "price": {"value": 12500000, "confidence": 0.95, "source_quote": "12 миллионов 500"},
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n\n"
        + "Текущая карточка объекта:\n"
        + json.dumps(existing, ensure_ascii=False, indent=2)
        + "\n\n"
        + "Транскрипт голосовой заметки:\n"
        + (snapshot.property.transcript or "")
    )
    return system_prompt, user_prompt


def build_copy_prompts(snapshot: PropertySnapshot, platforms: list[Platform]) -> tuple[str, str]:
    property_payload = snapshot.property.model_dump(mode="json")
    platform_rules = {platform.value: PLATFORM_COPY_RULES[platform] for platform in platforms}
    system_prompt = (
        "Ты senior-copywriter для российского рынка вторичной жилой недвижимости. "
        "Пишешь для риелтора, а не для инфоцыганского лендинга. Тон спокойный, уверенный, фактический. "
        "Нельзя выдумывать факты, приписывать юридические гарантии, ипотечные обещания, точную транспортную доступность, "
        "контакты, ссылки, скидки, которых нет в карточке. Верни только JSON. "
        "Сначала делай базовый текст, потом адаптируй его под площадки без нарушения их ограничений."
    )
    user_prompt = (
        "Нужен JSON формата:\n"
        + json.dumps(
            {
                "titles": ["3 title variants"],
                "description_short": "140-260 chars",
                "description_full": "700-1600 chars",
                "highlights": ["4-6 highlights"],
                "platform_copy": {
                    platform.value: {
                        "title": "platform title",
                        "description": "platform description",
                        "highlights": ["platform highlights"],
                    }
                    for platform in platforms
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n\n"
        + "Правила по площадкам:\n"
        + json.dumps(platform_rules, ensure_ascii=False, indent=2)
        + "\n\n"
        + "Карточка объекта:\n"
        + json.dumps(property_payload, ensure_ascii=False, indent=2)
        + "\n\n"
        + "Ограничения для текста:\n"
        + "- Не используй HTML.\n"
        + "- Не включай телефон, email, ссылки, hashtags.\n"
        + "- Не пиши 'лучшая', 'идеальная', 'срочно' без фактического основания.\n"
        + "- Не повторяй одну и ту же мысль разными словами.\n"
        + "- Делай текст удобным для последующей XML/YRL генерации."
    )
    return system_prompt, user_prompt


def normalize_extraction_payload(payload: dict[str, Any]) -> dict[str, AIFieldValue]:
    normalized: dict[str, AIFieldValue] = {}
    for key in EXTRACTION_SCHEMA_KEYS:
        raw_value = payload.get(key)
        if not isinstance(raw_value, dict):
            continue
        value = normalize_field_value(key, raw_value.get("value"))
        confidence = normalize_confidence(raw_value.get("confidence"))
        source_quote = raw_value.get("source_quote")
        normalized[key] = AIFieldValue(
            value=value,
            confidence=confidence,
            source_quote=str(source_quote).strip() if source_quote is not None else None,
        )
    return normalized


def normalize_field_value(key: str, value: Any) -> Any:
    if value is None:
        return None
    if key in {"rooms", "floor", "floors_total", "price", "year_built"}:
        return coerce_int(value)
    if key in {"area_total", "area_living", "area_kitchen", "ceiling_height", "latitude", "longitude"}:
        return coerce_float(value)
    if key in ENUM_NORMALIZERS:
        normalized_key = str(value).strip().lower()
        return ENUM_NORMALIZERS[key].get(normalized_key)
    if isinstance(value, str):
        compact = value.strip()
        return compact or None
    return value


def normalize_confidence(value: Any) -> float | None:
    numeric = coerce_float(value)
    if numeric is None:
        return None
    return max(0.0, min(1.0, numeric))


def coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        digits = re.sub(r"[^\d\-]", "", value)
        if digits in {"", "-"}:
            return None
        return int(digits)
    return None


def coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().replace(",", ".")
        cleaned = re.sub(r"[^0-9.\-]", "", cleaned)
        if cleaned in {"", "-", ".", "-."}:
            return None
        return float(cleaned)
    return None


def build_ai_provider(settings: Settings) -> AIProvider:
    fallback = MockAIProvider()
    if settings.ai_provider.lower() == "ollama":
        return OllamaAIProvider(settings, fallback)
    return fallback


def build_stt_provider(settings: Settings) -> SpeechToTextProvider:
    if (
        settings.stt_provider.lower() == "whisper_cpp"
        and settings.whisper_cpp_binary
        and settings.whisper_cpp_model_path
    ):
        return WhisperCppSpeechToTextProvider(settings.whisper_cpp_binary, settings.whisper_cpp_model_path)
    return MockSpeechToTextProvider()
