from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable
from xml.etree import ElementTree as ET

from .ai import sanitize_text, trimmed
from .models import Platform, PlatformCopy, PlatformPayloadRecord, PlatformValidationResult, Profile, PropertySnapshot, PublicationChannel


YRL_NAMESPACE = "http://webmaster.yandex.ru/schemas/feed/realty/2010-06"


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
        return {
            f"platforms/{self.platform.value}.{self.feed_extension}": single_feed,
            f"text/{self.platform.value}.txt": listing_copy,
            f"instructions/{self.platform.value}.md": manual_notes,
        }

    def manual_notes(self, profile: Profile) -> str:
        return (
            f"# {self.platform.value}\n\n"
            f"Канал для этого аккаунта: {self.validation_channel(profile).value}.\n"
            "Если площадка не принимает прямой URL-фид из вашего кабинета, используйте XML/YRL из архива "
            "и завершите публикацию вручную на стороне площадки."
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
