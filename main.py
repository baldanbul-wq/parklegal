from __future__ import annotations

import os
import re
import time
import uuid
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
import asyncio

from app.courts_service import _CACHE as COURTS_CACHE
from app.courts_service import refresh_courts, refresh_loop, find_court_by_latlon
from app.geocoder import geocode_address
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from docx import Document
from docx.shared import Pt
from docx.oxml.ns import qn

# Настройка логгера
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# --- патч для кэша судов ---
from asyncio import Event
COURTS_READY = Event()

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"
STORAGE_DIR = BASE_DIR / "storage"

TPL_MADI = str(TEMPLATES_DIR / "Шаблон жалобы МАДИ.docx")
TPL_AMPP = str(TEMPLATES_DIR / "Шаблон жалобы ГКУ АМПП.docx")

TTL_SECONDS = int(os.getenv("DOCGEN_TTL_SECONDS", str(2 * 60 * 60)))
PUBLIC_BASE = os.getenv("DOCGEN_PUBLIC_BASE", "http://192.168.1.6")

def _choose_template_by_number(number: str) -> str:
    if number.startswith("0356"):
        return TPL_MADI
    if number.startswith("0355"):
        return TPL_AMPP
    return ""

def _replace_text_in_paragraph(paragraph, mapping: dict):
    """
    Безопасная замена плейсхолдеров в каждом Run,
    с сохранением шрифта Times New Roman 12pt
    """
    for run in paragraph.runs:
        for key, val in mapping.items():
            if key in run.text:
                run.text = run.text.replace(key, val)
                # Сохраняем шрифт
                run.font.name = "Times New Roman"
                run.font.size = Pt(12)
                run._element.rPr.rFonts.set(qn("w:ascii"), "Times New Roman")
                run._element.rPr.rFonts.set(qn("w:hAnsi"), "Times New Roman")
                run._element.rPr.rFonts.set(qn("w:eastAsia"), "Times New Roman")
                run._element.rPr.rFonts.set(qn("w:cs"), "Times New Roman")

def _replace_in_cell(cell, mapping: dict):
    for p in cell.paragraphs:
        _replace_text_in_paragraph(p, mapping)

def _replace_everywhere(doc: Document, mapping: dict):
    for p in doc.paragraphs:
        _replace_text_in_paragraph(p, mapping)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                _replace_in_cell(cell, mapping)
    for section in doc.sections:
        for hf in (section.header, section.footer):
            if hf:
                for p in hf.paragraphs:
                    _replace_text_in_paragraph(p, mapping)
                for table in getattr(hf, "tables", []):
                    for row in table.rows:
                        for cell in row.cells:
                            _replace_in_cell(cell, mapping)

def _cleanup_storage():
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for p in STORAGE_DIR.glob("*.docx"):
        try:
            if now - p.stat().st_mtime > TTL_SECONDS:
                p.unlink(missing_ok=True)
        except Exception:
            pass

def _safe_filename(s: str) -> str:
    s = s.strip()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^A-Za-z0-9А-Яа-яЁё_\-\.]+", "", s)
    return s[:80] or "doc"

class GenerateRequest(BaseModel):
    number: str = Field(..., min_length=4, max_length=64)
    date: str = Field(..., min_length=4, max_length=32)
    address: str | None = None



@asynccontextmanager
async def lifespan(app: FastAPI):
    async def _warmup():
        try:
            await refresh_courts(force=True)
            logger.info("[courts] warmup ok")
            COURTS_READY.set()
        except Exception as e:
            logger.error("[courts] warmup failed: %s", e)
    asyncio.create_task(refresh_loop())
    await _warmup()

    yield # starting app


app = FastAPI(title="DocGen", version="1.0.0")

# jschatten: on_event deprectaed, лучше в lifespan
# @app.on_event("startup")
# async def startup():
#     async def _warmup():
#         try:
#             await refresh_courts(force=True)
#             logger.info("[courts] warmup ok")
#             COURTS_READY.set()
#         except Exception as e:
#             logger.error("[courts] warmup failed: %s", e)
#     asyncio.create_task(refresh_loop())
#     await _warmup()


# jschatten: Дублирующий код
# # === COURTS WARMUP (ParkLegal) ===
# import asyncio as _courts_asyncio

# @app.on_event("startup")
# async def _courts_warmup_on_startup():
#     # импорт внутри, чтобы не зависеть от порядка импортов в файле
#     from app.courts_service import refresh_courts, refresh_loop
#     await refresh_courts(force=True)
#     _courts_asyncio.create_task(refresh_loop())
#     logger.info("[courts] warmup ok")

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
        if not hit:
            logger.debug("⚠️ Суд не найден для этих координат")
            return "", "", "Суд не удалось определить автоматически. Проверь адрес или впиши суд вручную."
        logger.debug("find_court_by_latlon -> %r", hit)
        # Возвращаем значения прямо
        return hit.full_name, hit.address, None
    except Exception as e:
        logger.debug("Ошибка при определении суда: %s", e)
        return "", "", "Не удалось автоматически определить суд. Проверь адрес или впиши суд вручную."



@app.post("/generate")
async def generate(payload: GenerateRequest):
    _cleanup_storage()

    number = payload.number.strip()
    date_str = payload.date.strip()
    address = (payload.address or "").strip()

    tpl_path = _choose_template_by_number(number)
    if not tpl_path or not os.path.exists(tpl_path):
        raise HTTPException(
            status_code=400,
            detail="Не удалось определить шаблон: номер должен начинаться с 0356… (МАДИ) или 0355… (АМПП), "
                   "и файлы шаблонов должны лежать в /opt/docgen/templates."
        )

    msk_today = datetime.now(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y")

    # === ожидание заполнения судов ===
    MAX_WAIT = 5.0  # секунд
    INTERVAL = 0.1  # проверка каждые 100 мс

    start_time = time.time()
    court_name, court_address, warning = await resolve_court_fields(address)

    while not court_name or not court_address:
        await asyncio.sleep(INTERVAL)
        if time.time() - start_time > MAX_WAIT:
            raise HTTPException(
                status_code=500,
                detail="Не удалось получить данные о суде вовремя. Проверь адрес или попробуй позже."
            )
        court_name, court_address, warning = await resolve_court_fields(address)

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
