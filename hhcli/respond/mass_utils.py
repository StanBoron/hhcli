from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Iterable
from pathlib import Path

from .types import RespondResult


# --------- helpers: ids parsing ---------
def _iter_lines(text: str) -> Iterable[str]:
    for line in text.splitlines():
        yield line.strip()


def _extract_first_int_token(s: str) -> str | None:
    m = re.search(r"(?:^|\D)(\d+)(?:\D|$)", s)
    return m.group(1) if m else None


def extract_ids_from_text(text: str) -> list[str]:
    ids: list[str] = []
    for token in re.split(r"[\s,;]+", text.strip()):
        if not token:
            continue
        if token.isdigit():
            ids.append(token)
        else:
            m = re.search(r"(?:^|\D)(\d+)(?:\D|$)", token)
            if m:
                ids.append(m.group(1))
    return ids


def _read_ids_from_txt(path: Path) -> list[str]:
    ids: list[str] = []
    for line in _iter_lines(path.read_text(encoding="utf-8")):
        if not line:
            continue
        if line.isdigit():
            ids.append(line)
        else:
            cand = _extract_first_int_token(line)
            if cand:
                ids.append(cand)
    return ids


def _read_ids_from_csv(path: Path) -> list[str]:
    ids: list[str] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            f.seek(0)
            for row in csv.reader(f):
                if not row:
                    continue
                token = _extract_first_int_token(row[0])
                if token:
                    ids.append(token)
            return ids
        fields = [name.lower() for name in reader.fieldnames]
        if "vacancy_id" in fields:
            key = reader.fieldnames[fields.index("vacancy_id")]
        elif "id" in fields:
            key = reader.fieldnames[fields.index("id")]
        else:
            key = reader.fieldnames[0]
        for row in reader:
            value = row.get(key, "")
            token = value if str(value).isdigit() else _extract_first_int_token(str(value))
            if token:
                ids.append(token)
    return ids


def _read_ids_from_tsv(path: Path) -> list[str]:
    buf = io.StringIO(path.read_text(encoding="utf-8"))
    reader = csv.DictReader(buf, delimiter="\t")
    ids: list[str] = []
    if not reader.fieldnames:
        return ids
    fields = [n.lower() for n in reader.fieldnames]
    if "vacancy_id" in fields:
        key = reader.fieldnames[fields.index("vacancy_id")]
    elif "id" in fields:
        key = reader.fieldnames[fields.index("id")]
    else:
        key = reader.fieldnames[0]
    for row in reader:
        value = row.get(key, "")
        token = value if str(value).isdigit() else _extract_first_int_token(str(value))
        if token:
            ids.append(token)
    return ids


def _read_ids_from_jsonl(path: Path) -> list[str]:
    ids: list[str] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if isinstance(obj, str | int):
                ids.append(str(obj))
            elif isinstance(obj, dict):
                cand = obj.get("vacancy_id") or obj.get("id")
                if cand:
                    ids.append(str(cand))
            else:
                cand = _extract_first_int_token(str(obj))
                if cand:
                    ids.append(cand)
    return ids


def read_ids_from_file(path: Path) -> list[str]:
    sfx = path.suffix.lower()
    if sfx in (".txt", ""):
        return _read_ids_from_txt(path)
    if sfx == ".csv":
        return _read_ids_from_csv(path)
    if sfx in (".tsv", ".tab"):
        return _read_ids_from_tsv(path)
    if sfx in (".jsonl", ".ndjson"):
        return _read_ids_from_jsonl(path)
    raise ValueError(f"Unsupported file extension: {sfx}")


def read_ids_from_bytes(name: str, data: bytes) -> list[str]:
    name = (name or "").lower()
    if name.endswith(".txt") or "." not in name:
        return extract_ids_from_text(data.decode("utf-8", errors="ignore"))
    if name.endswith(".csv"):
        import pandas as pd

        df = pd.read_csv(io.BytesIO(data))
    elif name.endswith(".tsv") or name.endswith(".tab"):
        import pandas as pd

        df = pd.read_csv(io.BytesIO(data), sep="\t")
    elif name.endswith(".jsonl") or name.endswith(".ndjson"):
        ids: list[str] = []
        for line in data.decode("utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if isinstance(obj, str | int):
                ids.append(str(obj))
            elif isinstance(obj, dict):
                cand = obj.get("vacancy_id") or obj.get("id")
                if cand:
                    ids.append(str(cand))
            else:
                m = re.search(r"(?:^|\D)(\d+)(?:\D|$)", str(obj))
                if m:
                    ids.append(m.group(1))
        return ids
    else:
        return []

    cols = [c.lower() for c in df.columns]
    key = "vacancy_id" if "vacancy_id" in cols else ("id" if "id" in cols else df.columns[0])
    series = df[key]
    out: list[str] = []
    for v in series:
        s = str(v)
        if s.isdigit():
            out.append(s)
        else:
            m = re.search(r"(?:^|\D)(\d+)(?:\D|$)", s)
            if m:
                out.append(m.group(1))
    return [x for x in out if x]


# --------- API helpers ---------
def get_vacancy_meta(vacancy_id: str) -> dict:
    try:
        from hhcli.api import vacancies as vacancies_api
    except Exception:  # pragma: no cover
        return {}
    try:
        return vacancies_api.get_vacancy(vacancy_id)  # type: ignore[attr-defined]
    except Exception:
        return {}


def vacancy_requires_letter(meta: dict) -> bool:
    return bool(meta.get("response_letter_required"))


def vacancy_has_required_test(meta: dict) -> bool:
    test = meta.get("test")
    if isinstance(test, dict) and test.get("required"):
        return True
    return bool(meta.get("has_test"))


def resume_allowed_for_vacancy(vacancy_id: str, resume_id: str) -> bool:
    try:
        from hhcli.api import vacancies as vacancies_api
    except Exception:  # pragma: no cover
        return True
    try:
        data = vacancies_api.vacancy_resumes(vacancy_id)  # type: ignore[attr-defined]
        items = (data or {}).get("items") or []
        return any(str(it.get("id")) == str(resume_id) for it in items)
    except Exception:
        return True


def send_response(vacancy_id: str, resume_id: str, message: str | None) -> RespondResult:
    try:
        from hhcli.api import negotiations as negotiations_api
    except Exception:  # pragma: no cover
        return RespondResult(
            vacancy_id=vacancy_id, status="ok", http_code=201, negotiation_id="local"
        )
    try:
        payload = {"vacancy_id": str(vacancy_id), "resume_id": str(resume_id)}
        if message:
            payload["message"] = message
        resp = negotiations_api.create_response(**payload)  # type: ignore[attr-defined]
        negotiation_id = (
            resp.get("id") or resp.get("negotiation_id") if isinstance(resp, dict) else None
        )
        request_id = resp.get("request_id") if isinstance(resp, dict) else None
        return RespondResult(
            vacancy_id=vacancy_id,
            status="ok",
            http_code=201,
            negotiation_id=negotiation_id,
            request_id=request_id,
        )
    except Exception as err:
        http_code = getattr(err, "status_code", None) or getattr(err, "code", None)
        request_id = getattr(err, "request_id", None)
        return RespondResult(
            vacancy_id=vacancy_id,
            status="error",
            http_code=http_code,
            error=str(err),
            request_id=request_id,
        )
