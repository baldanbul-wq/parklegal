from __future__ import annotations

import os
import time
import uuid
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import uvicorn

from docx import Document

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from app.courts_service import find_court_by_latlon, NEW_COURTS, _rebuild_cache, fetch_courts_list
from app.geocoder import geocode_address
from app.models import GenerateRequest
from app.config import settings
from app.logger import logger

from app.utils import (
    _choose_template_by_number,
    _replace_everywhere,
    _cleanup_storage,
    _safe_filename,
)

# --- патч для кэша судов ---
from asyncio import Event
COURTS_READY = Event()

# jschatten: отдельная загрузка, чтобы можно было из окружения брать
TTL_SECONDS = settings.ttl_seconds
PUBLIC_BASE = settings.public_base
BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = settings.templates_dir
STORAGE_DIR = settings.storage_dir

TPL_MADI = str(TEMPLATES_DIR / "Шаблон жалобы МАДИ.docx")
TPL_AMPP = str(TEMPLATES_DIR / "Шаблон жалобы ГКУ АМПП.docx")


# jschatten: on_event deprectaed, лучше в lifespan
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Загрузка данных о судах при старте...")
    try:
        courts = await fetch_courts_list()

        # Построить spatial индекс
        raw_dicts = [court.model_dump() for court in courts]
        _rebuild_cache(raw_dicts)
        logger.info("Кэш геометрий построен")

        COURTS_READY.set()
        logger.info("Сервис готов к работе")
    except Exception as e:
        logger.error("Ошибка при инициализации кэша судов: %s", e)
        # Можно не падать, но клиенты получат 503 до следующего авто-обновления

    yield # starting app


app = FastAPI(title="DocGen", version="1.0.0", lifespan=lifespan)


async def resolve_court_fields(address: str | None):
    """
    returns: (court_name, court_address, warning)
    Этот патч логирует процесс и подставляет суд, если он найден в кэше.
    """
    if not address or not address.strip():
        logger.debug("адрес пустой")
        return "", "", None

    try:
        lat, lon = await geocode_address(address)
        logger.debug("geocode_address(%r) -> (%r, %r)", address, lat, lon)
        hit = find_court_by_latlon(lat, lon)
        logger.debug("NEW_COURTS list is %s", NEW_COURTS)
        if not hit:
            logger.debug("Суд не найден для этих координат")
            return "", "", "Суд не удалось определить автоматически. Проверь адрес или впиши суд вручную."
        logger.debug("find_court_by_latlon -> %r", hit)
        # Возвращаем значения прямо
        return hit.full_name, hit.address, None
    except Exception as e:
        logger.debug("Ошибка при определении суда: %s", e)
        return "", "", "Не удалось автоматически определить суд. Проверь адрес или впиши суд вручную."



@app.post("/generate")
async def generate(payload: GenerateRequest):
    _cleanup_storage(STORAGE_DIR, TTL_SECONDS)

    number = payload.number.strip()
    date_str = payload.date.strip()
    address = (payload.address or "").strip()
    logger.info("Запрос на генерацию: number=%s, date=%s, address=%r", number, date_str, address)
    tpl_path = _choose_template_by_number(number, TPL_MADI, TPL_AMPP)
    if not tpl_path or not os.path.exists(tpl_path):
        raise HTTPException(
            status_code=400,
            detail=(
                "Не удалось определить шаблон: номер должен начинаться с 0356… (МАДИ) "
                f"или 0355… (АМПП), и файлы шаблонов должны лежать в {TEMPLATES_DIR}"
            ),
        )

    msk_today = datetime.now(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y")

    court_name, court_address, warning = await resolve_court_fields(address)


    # jschatten: Добавляем ожидание готовности кэша судов
    if not COURTS_READY.is_set():
        try:
            await asyncio.wait_for(COURTS_READY.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            raise HTTPException(
                status_code=503,
                detail="Сервис временно недоступен: данные о судах ещё не загружены. Попробуйте позже."
            )

    court_name, court_address, warning = await resolve_court_fields(address)

    if not court_name or not court_address:
        if warning:
            return {
                "download_url": None,
                "expires_in": 0,
                "warning": warning
            }
        else:
            raise HTTPException(
                status_code=422,
                detail="Не удалось определить суд по указанному адресу."
            )


    # mapping с гарантированно заполненными значениями
    mapping = {
        "[doc_date]": msk_today,
        "[resolution number]": number,
        "[date]": date_str,
        "[sudname]": court_name,
        "[sudadress]": court_address,
    }

    # --- лог для отладки ---
    logger.debug("court_name: %r", court_name)
    logger.debug("court_address: %r", court_address)
    logger.debug("mapping: %r", {k: repr(v) for k,v in mapping.items()})

    doc = Document(tpl_path)
    _replace_everywhere(doc, mapping)

    token = uuid.uuid4().hex
    out_name = f"zhaloba_{number[-6:]}_{token}.docx"
    out_path = STORAGE_DIR / _safe_filename(out_name)
    doc.save(str(out_path))

    download_url = f"{PUBLIC_BASE}/download/{out_path.name}"
    resp = {"download_url": download_url, "expires_in": TTL_SECONDS}
    if warning:
        resp["warning"] = warning
    return resp


@app.get("/download/{filename}")
def download(filename: str):
    _cleanup_storage()

    if "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    file_path = STORAGE_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found or expired")

    return FileResponse(
        path=str(file_path),
        filename=file_path.name,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

# jschatten Тут обычно health-check и вот это всё, типа живое
@app.get("/")
def root():
    return JSONResponse( {"status": "ok"}, status_code=200)


# jschatten для запуска через кончоль, попроще: python run.py
if __name__ == "__main__":
    uvicorn.run("main:app", host=settings.host, port=settings.port, reload=True)
