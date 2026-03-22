# app/utils.py
from pathlib import Path
import re
import time
from typing import Dict

from docx import Document
from docx.shared import Pt
from docx.oxml.ns import qn


def _choose_template_by_number(number: str, tpl_madi: str, tpl_ampp: str) -> str:
    """
    Выбирает путь к шаблону в зависимости от префикса номера.

    :param number: Номер документа
    :param tpl_madi: Путь к шаблону МАДИ
    :param tpl_ampp: Путь к шаблону АМПП
    :return: Путь к выбранному шаблону или пустая строка
    """
    if number.startswith("0356"):
        return tpl_madi
    if number.startswith("0355"):
        return tpl_ampp
    return ""


def _replace_text_in_paragraph(paragraph, mapping: Dict[str, str]):
    """
    Безопасная замена плейсхолдеров в каждом Run,
    с сохранением шрифта Times New Roman 12pt.
    """
    for run in paragraph.runs:
        for key, val in mapping.items():
            if key in run.text:
                run.text = run.text.replace(key, val)
                # Сохраняем шрифт
                run.font.name = "Times New Roman"
                run.font.size = Pt(12)
                rpr = run._element.rPr
                rpr.rFonts.set(qn("w:ascii"), "Times New Roman")
                rpr.rFonts.set(qn("w:hAnsi"), "Times New Roman")
                rpr.rFonts.set(qn("w:eastAsia"), "Times New Roman")
                rpr.rFonts.set(qn("w:cs"), "Times New Roman")


def _replace_in_cell(cell, mapping: Dict[str, str]):
    """Замена текста во всех абзацах ячейки."""
    for paragraph in cell.paragraphs:
        _replace_text_in_paragraph(paragraph, mapping)


def _replace_everywhere(doc: Document, mapping: Dict[str, str]):
    """Рекурсивная замена текста по всему документу: тело, таблицы, колонтитулы."""
    # Основной текст
    for paragraph in doc.paragraphs:
        _replace_text_in_paragraph(paragraph, mapping)

    # Таблицы
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                _replace_in_cell(cell, mapping)

    # Колонтитулы
    for section in doc.sections:
        for header_footer in (section.header, section.footer):
            if header_footer is None:
                continue
            for paragraph in header_footer.paragraphs:
                _replace_text_in_paragraph(paragraph, mapping)
            for table in getattr(header_footer, "tables", []):
                for row in table.rows:
                    for cell in row.cells:
                        _replace_in_cell(cell, mapping)


def _cleanup_storage(storage_dir: Path, ttl_seconds: int):
    """
    Удаляет старые .docx файлы из директории хранения.

    :param storage_dir: Директория для очистки
    :param ttl_seconds: Время жизни файла (в секундах)
    """
    storage_dir.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for file_path in storage_dir.glob("*.docx"):
        try:
            if now - file_path.stat().st_mtime > ttl_seconds:
                file_path.unlink(missing_ok=True)
        except Exception as e:
            # Лучше логировать, чем молчать
            from logger import logger
            logger.warning(f"Не удалось удалить файл {file_path}: {e}")


def _safe_filename(s: str) -> str:
    """
    Преобразует строку в безопасное имя файла.
    Удаляет недопустимые символы, заменяет пробелы на подчёркивания.

    :param s: Исходная строка
    :return: Очищенная строка длиной до 80 символов
    """
    s = s.strip()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^A-Za-z0-9А-Яа-яЁё_\-\.]+", "", s)
    return s[:80] or "doc"


__all__ = [
    "_choose_template_by_number",
    "_replace_text_in_paragraph",
    "_replace_in_cell",
    "_replace_everywhere",
    "_cleanup_storage",
    "_safe_filename",
]