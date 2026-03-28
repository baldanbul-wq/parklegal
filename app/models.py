import json
from typing import List, Optional
from pydantic import BaseModel, Field, model_validator, RootModel


class GenerateRequest(BaseModel):
    number: str = Field(
        ...,
        min_length=4,
        max_length=64,
        description="Номер документа"
    )
    date: str = Field(
        ...,
        pattern=r"^\d{2}\.\d{2}\.\d{4}$",
        description="Дата в формате ДД.ММ.ГГГГ"
    )
    address: str = Field(
        None,
        description="Адрес"
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "number": "0356-SomeDoc",
                    "date": "01.01.2023",
                    "address": "оружейный переулок 41, Москва"
                    # 129281, г. Москва, ул. Летчика Бабушкина, д.39А
                }
            ]
        }
    }


class PolygonData(RootModel[List[List[List[float]]]]):
    """
    Модель для поля polygonData — представляет собой вложенный список координат.
    Автоматически парсит строку JSON.
    """
    @model_validator(mode="before")
    @classmethod
    def parse_json_string(cls, value):
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if not isinstance(parsed, list):
                    raise ValueError("polygonData must be a list of lists of lists")
                return parsed
            except (json.JSONDecodeError, TypeError) as e:
                raise ValueError(f"Invalid JSON in polygonData: {e}")
        return value

    def coordinates(self) -> List[List[List[float]]]:
        """Возвращает вложенный список координат."""
        return self.root


class CourtModel(BaseModel):
    id: str
    code: str
    number: int
    fullName: str
    shortName: str
    alias: str
    address: str
    phones: str
    businessHours: str
    email: str
    contact: str
    latitude: Optional[str] = None
    longitude: Optional[str] = None
    subwayStation: str
    polygonData: PolygonData
