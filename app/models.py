import json
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator

# class GenerateRequest(BaseModel):
#     number: str = Field(..., min_length=4, max_length=64)
#     # jschatten: Только формат ДД.ММ.ГГГГ, проверка регуляркой
#     date: str = Field(..., pattern=r"^\d{2}\.\d{2}\.\d{4}$")
#     address: str | None = None


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
    address: Optional[str] = Field(
        None,
        description="Адрес (необязательно)"
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


class PolygonData(BaseModel):
    __root__: List[List[List[float]]]

    @field_validator("__root__", mode="before")
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
        return self.__root__



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
    latitude: str  # Можно преобразовать в float, если нужно
    longitude: str  # Можно преобразовать в float, если нужно
    subwayStation: str
    polygonData: PolygonData
