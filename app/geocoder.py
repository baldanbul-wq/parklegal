import os
import re
import httpx
from app.logger import logger
from app.config import settings


YA_GEOCODER_API_KEY = settings.yandex_geocoder_api_key.get_secret_value()


def normalize_address(addr: str) -> str:
    """
    Приводим адрес к виду, максимально понятному геокодеру.
    """
    addr = re.sub(r"\s+", " ", (addr or "").strip())
    if not addr:
        return ""
    # Принудительно добавляем Москву, если пользователь не указал
    if "москва" not in addr.lower():
        addr = "Москва, " + addr
    return addr


async def geocode_address(address: str) -> tuple[float, float]:
    """
    Геокодирование адреса через Яндекс.Геокодер.
    Возвращает (lat, lon).
    """
    logger.debug("YANDEX_GEOCODER_API_KEY(%s)", YA_GEOCODER_API_KEY)
    if not YA_GEOCODER_API_KEY:
        raise RuntimeError("YANDEX_GEOCODER_API_KEY is not set")

    address = normalize_address(address)
    if not address:
        raise ValueError("Пустой адрес")

    url = "https://geocode-maps.yandex.ru/1.x/"
    params = {
        "apikey": YA_GEOCODER_API_KEY,
        "format": "json",
        "geocode": address,
        "results": 1,
        # Можно раскомментировать, если хочешь жёстче требовать дом:
        # "kind": "house",
    }

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        data = r.json()

    try:
        feature_members = data["response"]["GeoObjectCollection"]["featureMember"]
        if not feature_members:
            raise ValueError("Адрес не найден геокодером")

        pos = feature_members[0]["GeoObject"]["Point"]["pos"]  # "lon lat"
        lon_str, lat_str = pos.split()
        lat = float(lat_str)
        lon = float(lon_str)
        return lat, lon

    except Exception as e:
        raise ValueError(f"Ошибка разбора ответа Яндекс Геокодера: {e}") from e

