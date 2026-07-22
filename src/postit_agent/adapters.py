from __future__ import annotations

import html
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable
from xml.etree import ElementTree as ET

from .ai import sanitize_text, trimmed
from .models import Platform, PlatformCopy, PlatformPayloadRecord, PlatformValidationResult, Profile, PropertySnapshot, PublicationChannel


YRL_NAMESPACE = "http://webmaster.yandex.ru/schemas/feed/realty/2010-06"
CRITICAL_PENDING_FIELDS = {
    "city": "Город",
    "street": "Улица",
    "house": "Дом",
    "price": "Цена",
    "rooms": "Количество комнат",
    "area_total": "Общая площадь",
    "floor": "Этаж",
    "floors_total": "Этажность дома",
}


@dataclass
class ListingWithPayload:
    snapshot: PropertySnapshot
    payload: PlatformPayloadRecord


class BasePlatformAdapter:
    platform: Platform
    feed_extension = "xml"

    def validation_channel(self, profile: Profile) -> PublicationChannel:
        return PublicationChannel.url_feed

    def validate(self, snapshot: PropertySnapshot) -> PlatformValidationResult:
        errors: list[str] = []
        warnings: list[str] = []
        property_record = snapshot.property
        features = property_record.features
        contacts = property_record.contacts

        if not property_record.confirmed:
            errors.append("Карточка объекта не подтверждена пользователем.")
        if property_record.price is None:
            errors.append("Не указана цена объекта.")
        if not property_record.address.city:
            errors.append("Не указан город.")
        if not property_record.address.street:
            warnings.append("Улица не заполнена: часть площадок пропустит, часть отклонит объект.")
        if features.rooms is None:
            errors.append("Не указана комнатность.")
        if features.area_total is None:
            errors.append("Не указана общая площадь.")
        if features.floor is None or features.floors_total is None:
            errors.append("Не заполнены этаж и этажность дома.")
        if features.floor and features.floors_total and features.floor > features.floors_total:
            errors.append("Этаж объекта не может быть выше этажности дома.")
        if not property_record.description_base:
            errors.append("Не сгенерировано или не подтверждено базовое описание.")
        if len(snapshot.photos) < 5:
            errors.append("Для публикации нужно минимум 5 фотографий.")
        if not (contacts.contact_phone or snapshot.profile.contact_phone):
            errors.append("Не заполнен контактный телефон.")

        for evidence in snapshot.evidence:
            if evidence.field_name not in CRITICAL_PENDING_FIELDS:
                continue
            if evidence.is_confirmed or evidence.is_rejected:
                continue
            errors.append(
                f"{CRITICAL_PENDING_FIELDS[evidence.field_name]} заполнено AI и требует подтверждения пользователем."
            )

        return PlatformValidationResult(
            platform=self.platform,
            ready=not errors,
            channel=self.validation_channel(snapshot.profile),
            errors=errors,
            warnings=warnings,
        )

    def build_payload(self, snapshot: PropertySnapshot, copy: PlatformCopy, validation: PlatformValidationResult) -> dict[str, Any]:
        property_record = snapshot.property
        return {
            "external_id": property_record.external_id or property_record.id,
            "address": property_record.address.one_line(),
            "city": property_record.address.city,
            "title": copy.title,
            "description": copy.description,
            "price": property_record.price,
            "currency": property_record.currency,
            "rooms": property_record.features.rooms,
            "area_total": property_record.features.area_total,
            "area_kitchen": property_record.features.area_kitchen,
            "area_living": property_record.features.area_living,
            "floor": property_record.features.floor,
            "floors_total": property_record.features.floors_total,
            "building_type": property_record.features.building_type,
            "photos": [photo.public_url for photo in snapshot.photos],
            "contact_phone": property_record.contacts.contact_phone or snapshot.profile.contact_phone,
            "contact_name": property_record.contacts.contact_name or snapshot.profile.name,
            "channel": validation.channel.value,
        }

    def build_feed(self, listings: Iterable[ListingWithPayload]) -> str:
        raise NotImplementedError

    def build_export_entries(self, listing: ListingWithPayload) -> dict[str, bytes]:
        single_feed = self.build_feed([listing]).encode("utf-8")
        manual_notes = self.manual_notes(listing.snapshot.profile).encode("utf-8")
        listing_copy = (
            f"{listing.payload.title}\n\n{listing.payload.description}\n\n"
            + "\n".join(f"- {item}" for item in listing.payload.payload.get("highlights", []))
        ).encode("utf-8")
        payload_json = json.dumps(listing.payload.model_dump(mode="json"), ensure_ascii=False, indent=2).encode("utf-8")
        manual_html = self.manual_html(listing).encode("utf-8")
        return {
            f"platforms/{self.platform.value}.{self.feed_extension}": single_feed,
            f"text/{self.platform.value}.txt": listing_copy,
            f"instructions/{self.platform.value}.md": manual_notes,
            f"payloads/{self.platform.value}.json": payload_json,
            f"html/{self.platform.value}.html": manual_html,
        }

    def manual_notes(self, profile: Profile) -> str:
        create_url_note = ""
        if create_url := getattr(profile, "model_extra", None):  # pragma: no cover - defensive
            create_url_note = str(create_url)
        return (
            f"# {self.platform.value}\n\n"
            f"Канал для этого аккаунта: {self.validation_channel(profile).value}.\n"
            "Если площадка не принимает прямой URL-фид из вашего кабинета, используйте XML/YRL из архива "
            "и завершите публикацию вручную на стороне площадки."
        )

    def manual_html(self, listing: ListingWithPayload) -> str:
        payload = listing.payload.payload
        create_url = payload.get("create_url")
        link_block = (
            f'<p><a href="{html.escape(create_url)}" target="_blank" rel="noreferrer">Открыть экран создания объявления</a></p>'
            if create_url
            else "<p>Ссылка на экран создания объявления не настроена в конфигурации.</p>"
        )
        fields = "".join(
            f"<tr><th>{html.escape(str(key))}</th><td>{html.escape(str(value))}</td></tr>"
            for key, value in payload.items()
            if key not in {"photos", "highlights"}
        )
        highlights = "".join(f"<li>{html.escape(str(item))}</li>" for item in payload.get("highlights", []))
        photos = "".join(
            f'<li><a href="{html.escape(url)}" target="_blank" rel="noreferrer">{html.escape(url)}</a></li>'
            for url in payload.get("photos", [])
        )
        return (
            "<!doctype html><html lang=\"ru\"><head><meta charset=\"utf-8\"><title>Assisted export</title>"
            "<style>body{font-family:system-ui,sans-serif;max-width:980px;margin:0 auto;padding:24px;line-height:1.5}"
            "table{border-collapse:collapse;width:100%}th,td{border:1px solid #ddd;padding:8px;text-align:left}"
            "pre{white-space:pre-wrap;background:#f6f6f6;padding:12px;border-radius:8px}</style></head><body>"
            f"<h1>{html.escape(listing.payload.title)}</h1>"
            f"<p><strong>Площадка:</strong> {html.escape(self.platform.value)}</p>"
            f"<p><strong>Канал:</strong> {html.escape(listing.payload.validation.channel.value)}</p>"
            f"{link_block}"
            f"<h2>Описание</h2><pre>{html.escape(listing.payload.description)}</pre>"
            f"<h2>Преимущества</h2><ul>{highlights}</ul>"
            f"<h2>Подготовленные поля</h2><table>{fields}</table>"
            f"<h2>Фотографии</h2><ul>{photos}</ul>"
            "</body></html>"
        )

    @staticmethod
    def _append_photos(parent: ET.Element, photo_urls: list[str], tag_name: str = "image") -> None:
        for url in photo_urls:
            ET.SubElement(parent, tag_name).text = url


class AvitoAdapter(BasePlatformAdapter):
    platform = Platform.avito

    def validation_channel(self, profile: Profile) -> PublicationChannel:
        account = profile.platform_accounts.get(self.platform.value)
        if account and account.autoload_enabled:
            return PublicationChannel.url_feed
        return PublicationChannel.user_assisted

    def build_feed(self, listings: Iterable[ListingWithPayload]) -> str:
        root = ET.Element("Ads", {"formatVersion": "3"})
        for listing in listings:
            payload = listing.payload.payload
            ad = ET.SubElement(root, "Ad")
            ET.SubElement(ad, "Id").text = payload["external_id"]
            ET.SubElement(ad, "Category").text = "Квартиры"
            ET.SubElement(ad, "OperationType").text = "Продам"
            ET.SubElement(ad, "Address").text = payload["address"]
            ET.SubElement(ad, "Description").text = sanitize_text(listing.payload.description)
            ET.SubElement(ad, "Price").text = str(payload["price"] or "")
            ET.SubElement(ad, "Rooms").text = str(payload["rooms"] or "")
            ET.SubElement(ad, "Square").text = str(payload["area_total"] or "")
            ET.SubElement(ad, "Floor").text = str(payload["floor"] or "")
            ET.SubElement(ad, "Floors").text = str(payload["floors_total"] or "")
            images = ET.SubElement(ad, "Images")
            for url in payload["photos"]:
                ET.SubElement(images, "Image", {"url": url})
        return xml_string(root)


class CianAdapter(BasePlatformAdapter):
    platform = Platform.cian

    def build_feed(self, listings: Iterable[ListingWithPayload]) -> str:
        root = ET.Element("Feed")
        ET.SubElement(root, "Feed_Version").text = "2"
        for listing in listings:
            payload = listing.payload.payload
            obj = ET.SubElement(root, "Object")
            ET.SubElement(obj, "Category").text = "flatSale"
            ET.SubElement(obj, "ExternalId").text = payload["external_id"]
            ET.SubElement(obj, "Description").text = trimmed(sanitize_text(listing.payload.description), 3000)
            ET.SubElement(obj, "Address").text = payload["address"]
            ET.SubElement(obj, "FlatRoomsCount").text = str(payload["rooms"] if payload["rooms"] is not None else "")
            ET.SubElement(obj, "TotalArea").text = str(payload["area_total"] or "")
            ET.SubElement(obj, "LivingArea").text = str(payload["area_living"] or "")
            ET.SubElement(obj, "KitchenArea").text = str(payload["area_kitchen"] or "")
            ET.SubElement(obj, "FloorNumber").text = str(payload["floor"] or "")
            ET.SubElement(obj, "FloorsCount").text = str(payload["floors_total"] or "")
            ET.SubElement(obj, "Price").text = str(payload["price"] or "")
            photos = ET.SubElement(obj, "Photos")
            for url in payload["photos"]:
                photo = ET.SubElement(photos, "PhotoSchema")
                ET.SubElement(photo, "FullUrl").text = url
        return xml_string(root)


class YandexRealtyAdapter(BasePlatformAdapter):
    platform = Platform.yandex_realty

    def build_feed(self, listings: Iterable[ListingWithPayload]) -> str:
        root = ET.Element("realty-feed", {"xmlns": YRL_NAMESPACE})
        ET.SubElement(root, "generation-date").text = datetime.now().astimezone().isoformat(timespec="seconds")
        for listing in listings:
            payload = listing.payload.payload
            offer = ET.SubElement(root, "offer", {"internal-id": payload["external_id"]})
            ET.SubElement(offer, "type").text = "продажа"
            ET.SubElement(offer, "property-type").text = "жилая"
            ET.SubElement(offer, "category").text = "квартира"
            ET.SubElement(offer, "creation-date").text = datetime.now().astimezone().isoformat(timespec="seconds")
            location = ET.SubElement(offer, "location")
            ET.SubElement(location, "country").text = "Россия"
            if payload["city"]:
                ET.SubElement(location, "locality-name").text = payload["city"]
            ET.SubElement(location, "address").text = payload["address"]
            sales_agent = ET.SubElement(offer, "sales-agent")
            ET.SubElement(sales_agent, "phone").text = payload["contact_phone"] or ""
            ET.SubElement(sales_agent, "category").text = "агентство"
            ET.SubElement(sales_agent, "name").text = payload["contact_name"] or "Агент"
            price = ET.SubElement(offer, "price")
            ET.SubElement(price, "value").text = str(payload["price"] or "")
            ET.SubElement(price, "currency").text = "RUR"
            ET.SubElement(offer, "description").text = sanitize_text(listing.payload.description)
            area = ET.SubElement(offer, "area")
            ET.SubElement(area, "value").text = str(payload["area_total"] or "")
            ET.SubElement(area, "unit").text = "кв. м"
            if payload["area_living"] is not None:
                living = ET.SubElement(offer, "living-space")
                ET.SubElement(living, "value").text = str(payload["area_living"])
                ET.SubElement(living, "unit").text = "кв. м"
            if payload["area_kitchen"] is not None:
                kitchen = ET.SubElement(offer, "kitchen-space")
                ET.SubElement(kitchen, "value").text = str(payload["area_kitchen"])
                ET.SubElement(kitchen, "unit").text = "кв. м"
            ET.SubElement(offer, "rooms").text = str(payload["rooms"] or "")
            ET.SubElement(offer, "floor").text = str(payload["floor"] or "")
            ET.SubElement(offer, "floors-total").text = str(payload["floors_total"] or "")
            self._append_photos(offer, payload["photos"], "image")
        return xml_string(root)


class YoulaAdapter(YandexRealtyAdapter):
    platform = Platform.youla

    def validation_channel(self, profile: Profile) -> PublicationChannel:
        account = profile.platform_accounts.get(self.platform.value)
        if account and account.autoload_enabled:
            return PublicationChannel.url_feed
        return PublicationChannel.manual_export

    def validate(self, snapshot: PropertySnapshot) -> PlatformValidationResult:
        result = super().validate(snapshot)
        if len(snapshot.photos) > 30:
            result.errors.append("Для Юлы в MVP держим лимит 30 фото на объявление.")
            result.ready = False
        return result


class DomclickAdapter(YandexRealtyAdapter):
    platform = Platform.domclick

    def validation_channel(self, profile: Profile) -> PublicationChannel:
        account = profile.platform_accounts.get(self.platform.value)
        if account and account.autoload_enabled:
            return PublicationChannel.url_feed
        return PublicationChannel.manual_export


def xml_string(root: ET.Element) -> str:
    return ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")


def build_adapters() -> dict[Platform, BasePlatformAdapter]:
    adapters: list[BasePlatformAdapter] = [
        AvitoAdapter(),
        CianAdapter(),
        YandexRealtyAdapter(),
        YoulaAdapter(),
        DomclickAdapter(),
    ]
    return {adapter.platform: adapter for adapter in adapters}
