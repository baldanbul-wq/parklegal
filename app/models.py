
from pydantic import BaseModel, Field

# class GenerateRequest(BaseModel):
#     number: str = Field(..., min_length=4, max_length=64)
#     # jschatten: Только формат ДД.ММ.ГГГГ, проверка регуляркой
#     date: str = Field(..., pattern=r"^\d{2}\.\d{2}\.\d{4}$")
#     address: str | None = None

from pydantic import BaseModel, Field
from typing import Optional

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
                    "number": "DOC-12345",
                    "date": "01.01.2023",
                    "address": "оружейный переулок 41, Москва"
                }
            ]
        }
    }
