"""Omit obsolete validation sidecars from remote Catalog exports."""

from io import BytesIO
from zipfile import BadZipFile, ZipFile

from fastapi import HTTPException


def is_validation(path):
    return path.endswith(".validation.json")


def export_files(files):
    return [file for file in files if not is_validation(file["path"])]


def export_archive(content):
    result = BytesIO()
    try:
        with ZipFile(BytesIO(content)) as source, ZipFile(result, "w") as target:
            if not any(is_validation(entry.filename) for entry in source.infolist()):
                return content
            for entry in source.infolist():
                if not is_validation(entry.filename):
                    target.writestr(entry, source.read(entry))
    except BadZipFile:
        raise HTTPException(502, "AutoTune 返回了无效导出 ZIP") from None
    return result.getvalue()
