from __future__ import annotations

import io
import json
import time
import re
from typing import Any

import pandas as pd
import streamlit as st

from hhcli.api import (
    areas as areas_api,
)
from hhcli.api import (
    dictionaries,
    negotiations,
    professional_roles,
    resumes,
    vacancies,
)
from hhcli.auth import build_oauth_url, exchange_code, set_tokens  # ← вот это важно
from hhcli.config import load_config, save_config
from hhcli.http import request
from hhcli.utils import format_salary, paginate_vacancies

st.set_page_config(page_title="HH.ru Search", layout="wide")

ID_RE = re.compile(r"^\d+$")

# ========================= Caching of dictionaries =========================


@st.cache_data(show_spinner=False)
def get_roles_cache() -> list[dict[str, Any]]:
    data = professional_roles.get_roles()
    roles_flat: list[dict[str, Any]] = []
    for group in data.get("categories", []):
        for r in group.get("roles", []):
            roles_flat.append({"id": int(r["id"]), "name": r["name"], "group": group["name"]})
    return roles_flat


@st.cache_data(show_spinner=False)
def get_schedules_cache() -> list[dict[str, str]]:
    data = dictionaries.get_dictionaries()
    sched = data.get("schedule", []) or []
    return [{"id": s["id"], "name": s["name"]} for s in sched]


@st.cache_data(show_spinner=False)
def get_area_children(area_id: int | None) -> list[dict[str, Any]]:
    if area_id is None:
        return areas_api.get_areas_tree()
    node = areas_api.get_area_node(area_id)
    return node.get("areas", [])


# ========================= Search helpers =========================


def search_dataframe(
    *,
    text: str,
    area: int | None,
    roles: list[int] | None,
    schedule: str | None,
    per_page: int,
    limit: int | None,
    experience: str | None,
    employment: list[str] | None,
    salary: int | None,
    currency: str | None,
    only_with_salary: bool,
    search_field: str | None,
    order_by: str | None,
    date_from: str | None,
    date_to: str | None,
    with_address: bool,
    include_details: bool = False,  # ← правильное объявление
) -> pd.DataFrame:
    def fetch(page: int, per_page_: int):
        return vacancies.search_vacancies(
            text=text or "",
            area=area,
            professional_role=roles if roles else None,
            schedule=schedule,
            experience=experience,
            employment=employment,
            salary=salary if salary else None,
            currency=currency if currency else None,
            only_with_salary="true" if only_with_salary else None,
            search_field=search_field,
            order_by=order_by,
            date_from=date_from,
            date_to=date_to,
            with_address="true" if with_address else None,
            per_page=per_page_,
            page=page,
        )

    rows: list[dict[str, Any]] = []
    for v in paginate_vacancies(fetch, per_page=per_page, limit=limit):
        salary_str = format_salary(v.get("salary"))
        emp = (v.get("employer") or {}).get("name", "")
        area_name = (v.get("area") or {}).get("name", "")
        row = {
            "id": v.get("id", ""),
            "title": v.get("name", ""),
            "employer": emp,
            "employer_id": (v.get("employer") or {}).get("id", ""),
            "salary": salary_str,
            "area": area_name,
            "published_at": v.get("published_at", ""),
            "url": v.get("alternate_url", ""),
            "schedule_id": (
                (v.get("schedule") or {}).get("id", "")
                if isinstance(v.get("schedule"), dict)
                else v.get("schedule", "")
            ),
            "employment_id": (
                (v.get("employment") or {}).get("id", "")
                if isinstance(v.get("employment"), dict)
                else v.get("employment", "")
            ),
            "address_city": (v.get("address") or {}).get("city", ""),
            "address_street": (v.get("address") or {}).get("street", ""),
            "address_raw": (v.get("address") or {}).get("raw", ""),
        }
        if include_details and row["id"]:
            try:
                det = vacancies.get_vacancy(str(row["id"])) or {}
                row["schedule_id"] = (det.get("schedule") or {}).get("id", row["schedule_id"])
                row["schedule_name"] = (det.get("schedule") or {}).get("name", "")
                row["employment_id"] = (det.get("employment") or {}).get("id", row["employment_id"])
                row["employment_name"] = (det.get("employment") or {}).get("name", "")
                exp = det.get("experience") or {}
                row["experience_id"] = exp.get("id", "")
                row["experience_name"] = exp.get("name", "")
                addr = det.get("address") or {}
                row["address_city"] = addr.get("city", row["address_city"])
                row["address_street"] = addr.get("street", row["address_street"])
                row["address_raw"] = addr.get("raw", row["address_raw"])
                ks = det.get("key_skills") or []
                row["key_skills"] = ", ".join(
                    sorted({(k or {}).get("name", "") for k in ks if (k or {}).get("name")})
                )
            except Exception:
                pass
        rows.append(row)

    return pd.DataFrame(rows)


def df_to_download(df: pd.DataFrame, fmt: str) -> tuple[bytes | None, str, str]:
    """
    Возвращает (data_bytes, mime, filename) для выбранного формата.
    fmt: 'CSV' | 'JSONL' | 'Parquet'
    """
    fmt_u = fmt.upper()
    if fmt_u == "CSV":
        return df.to_csv(index=False).encode("utf-8"), "text/csv; charset=utf-8", "vacancies.csv"
    if fmt_u == "JSONL":
        data = df.to_json(orient="records", lines=True, force_ascii=False)
        return data.encode("utf-8"), "application/json", "vacancies.jsonl"
    if fmt_u == "PARQUET":
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except Exception as e:
            st.error(f"Для Parquet нужны зависимости: pandas, pyarrow. {e}")
            return None, "", ""
        table = pa.Table.from_pandas(df)
        buf = io.BytesIO()
        pq.write_table(table, buf)
        return buf.getvalue(), "application/octet-stream", "vacancies.parquet"
    return None, "", ""


# ========================= UI helpers =========================


def area_picker(label: str) -> int | None:
    """
    Простой «двухуровневый» выбор area: страна -> регионы/города.
    """
    st.write(f"**{label}**")
    countries = get_area_children(None)
    c_map = {f"{c['name']} ({c['id']})": int(c["id"]) for c in countries}
    c_label = st.selectbox("Страна", [""] + list(c_map.keys()), index=0, key="country_select")
    if not c_label:
        return None
    country_id = c_map[c_label]
    children = get_area_children(country_id)
    if not children:
        return country_id
    ch_map = {f"{c['name']} ({c['id']})": int(c["id"]) for c in children}
    ch_label = st.selectbox(
        "Регион/город", [""] + list(ch_map.keys()), index=0, key="region_select"
    )
    if not ch_label:
        return country_id
    return ch_map[ch_label]


# ========================= OAuth block =========================


def oauth_ui():
    st.subheader("Вход в hh.ru (OAuth)")

    cfg = load_config()
    col1, col2, col3 = st.columns(3)
    with col1:
        client_id = st.text_input("Client ID", value=cfg.get("client_id", ""), type="default")
    with col2:
        client_secret = st.text_input(
            "Client Secret", value=cfg.get("client_secret", ""), type="password"
        )
    with col3:
        redirect_uri = st.text_input(
            "Redirect URI", value=cfg.get("redirect_uri", "http://localhost:8501")
        )

    if st.button("Сохранить настройки OAuth"):
        cfg["client_id"] = client_id.strip()
        cfg["client_secret"] = client_secret.strip()
        cfg["redirect_uri"] = redirect_uri.strip()
        save_config(cfg)
        st.success("Сохранено. Сгенерируйте ссылку ниже и авторизуйтесь.")

    st.caption("Скоупы: read + negotiations + resumes")

    # --- Ссылка на авторизацию ---
    if st.button("Сгенерировать ссылку на авторизацию"):
        try:
            auth_url = build_oauth_url()
            st.link_button("Перейти к авторизации на hh.ru", url=auth_url, type="primary")
            st.code(auth_url, language="text")
        except Exception as e:
            st.error(f"Не удалось сформировать ссылку: {e}")

    # --- Автозахват кода из query-параметров (новое/старое API Streamlit) ---
    code = None
    try:
        qp = st.query_params  # Streamlit >= 1.31
        code_val = qp.get("code")
        code = code_val if isinstance(code_val, str) else (code_val[0] if code_val else None)
    except Exception:
        qp = st.experimental_get_query_params()
        code_list = qp.get("code", [])
        code = code_list[0] if code_list else None

    if "oauth_done" not in st.session_state:
        st.session_state["oauth_done"] = False

    st.text_input("Код из редиректа (можно вставить вручную)", key="code_manual")
    manual_code = st.session_state.get("code_manual") or None
    final_code = manual_code or code

    if final_code and not st.session_state["oauth_done"]:
        with st.spinner("Обмениваю код на токены..."):
            try:
                exchange_code(final_code)
                st.session_state["oauth_done"] = True
                st.success("Токен получен и сохранён.")
            except Exception as e:
                st.error(f"Ошибка обмена кода: {e}")

    # --- Ручной ввод токенов ---
    with st.expander("Ввести токены вручную", expanded=False):
        st.caption(
            "Если у вас уже есть токены, вставьте их сюда. "
            "Если знаете только unix-время истечения (`access_expires_at`), укажите его — "
            "приложение посчитает `expires_in` автоматически."
        )
        at = st.text_input("access_token", type="password", key="manual_at")
        rt = st.text_input("refresh_token (опционально)", type="password", key="manual_rt")

        colA, colB = st.columns(2)
        with colA:
            expires_in = st.number_input(
                "expires_in (сек)",
                min_value=0,
                step=60,
                value=0,
                help="Сколько секунд осталось до истечения access_token. Можно оставить 0 и указать expires_at справа.",
                key="manual_expires_in",
            )
        with colB:
            expires_at_str = st.text_input(
                "access_expires_at (unix, опционально)",
                value="",
                help="Например: 1756723030. Если заполнено и expires_in=0 — будет пересчитано автоматически.",
                key="manual_expires_at",
            )

        if st.button("Сохранить токены", key="btn_save_tokens_manual"):
            try:
                exp_in_final = int(expires_in) if expires_in else 0
                if exp_in_final == 0 and expires_at_str.strip().isdigit():
                    exp_at = int(expires_at_str.strip())
                    exp_in_final = max(0, exp_at - int(time.time()))

                if not at.strip():
                    st.warning("Заполните access_token.")
                else:
                    set_tokens(at, rt or None, exp_in_final if exp_in_final > 0 else None)
                    st.success(
                        "Токены сохранены (~/.hhcli/config.json). Теперь можно проверить /me ниже."
                    )
            except Exception as e:
                st.error(f"Ошибка сохранения токенов: {e}")

    # --- Импорт токенов из JSON ---
    with st.expander("Импорт токенов из JSON-файла", expanded=False):
        st.caption(
            "Загрузите JSON с токенами. Поддерживаются форматы:\n"
            "1) { 'access_token': '...', 'refresh_token': '...', 'expires_in': 12345 }\n"
            "2) { 'token': { 'access_expires_at': 1756723030, 'access_token': '...', 'refresh_token': '...' } }"
        )
        uploaded = st.file_uploader(
            "Выберите JSON-файл", type=["json"], accept_multiple_files=False
        )
        if uploaded is not None:
            try:
                data = json.load(uploaded)

                # Унифицируем: поддерживаем вложенный объект token и/или прямые ключи
                token_obj = data.get("token") if isinstance(data, dict) else None
                base = token_obj or data or {}
                access_token = base.get("access_token")
                refresh_token = base.get("refresh_token")

                # expires_in: берём напрямую или считаем из access_expires_at
                expires_in_val = None
                if "expires_in" in base:
                    expires_in_val = int(base["expires_in"])
                elif "access_expires_at" in base:
                    exp_at = int(base["access_expires_at"])
                    expires_in_val = max(0, exp_at - int(time.time()))

                if not access_token:
                    st.error("В JSON не найден 'access_token'.")
                else:
                    set_tokens(access_token, refresh_token or None, expires_in_val)
                    st.success(
                        "Токены импортированы и сохранены (~/.hhcli/config.json). Ниже можно проверить /me."
                    )
            except Exception as e:
                st.error(f"Не удалось прочитать JSON: {e}")
    # --- Экспорт текущих токенов в JSON ---
    with st.expander("Экспорт токенов в JSON", expanded=False):
        st.caption(
            "Скачайте токены для переноса на другую машину. "
            "⚠️ Это секретные данные — храните файл в безопасном месте."
        )

        cfg_now = load_config()
        access_token = cfg_now.get("access_token") or ""
        refresh_token = cfg_now.get("refresh_token") or ""
        token_expires_at = int(cfg_now.get("token_expires_at") or 0)
        expires_in_now = max(0, token_expires_at - int(time.time())) if token_expires_at else None

        fmt = st.radio(
            "Формат файла",
            options=["Совместимый (nested token)", "Плоский (flat)"],
            index=0,
            horizontal=True,
            help="Оба формата поддерживаются нашим импортом.",
        )

        if fmt.startswith("Совместимый"):
            export_obj = {
                "token": {
                    "access_token": access_token,
                    "refresh_token": refresh_token or None,
                    "access_expires_at": token_expires_at or None,
                }
            }
        else:
            export_obj = {
                "access_token": access_token,
                "refresh_token": refresh_token or None,
                "expires_in": expires_in_now if expires_in_now is not None else 0,
            }

        export_name = st.text_input("Имя файла", value="hh_tokens.json")
        export_json = json.dumps(export_obj, ensure_ascii=False, indent=2)

        st.code(export_json, language="json")
        st.download_button(
            label="Скачать JSON",
            data=export_json.encode("utf-8"),
            file_name=export_name or "hh_tokens.json",
            mime="application/json",
        )

    # --- Быстрые проверки ---
    colA, colB = st.columns(2)
    with colA:
        if st.button("Проверить профиль (/me)"):
            try:
                me = request("GET", "/me", auth=True)
                st.json(me)
            except Exception as e:
                st.error(f"Ошибка запроса /me: {e}")
    with colB:
        if st.button("Показать мои резюме"):
            try:
                data = resumes.my_resumes()
                st.json(data)
            except Exception as e:
                st.error(f"Ошибка запроса резюме: {e}")


# ========================= Respond (negotiations) =========================

DAILY_APPLY_LIMIT = 200


def _get_apply_counters() -> dict:
    cfg = load_config()
    return cfg.setdefault("apply_counters", {"day_start": 0, "sent": 0})


def _bump_apply_counters(delta: int) -> None:
    cfg = load_config()
    counters = cfg.setdefault("apply_counters", {"day_start": 0, "sent": 0})
    now = int(time.time())
    if counters["day_start"] == 0 or now - counters["day_start"] >= 24 * 3600:
        counters["day_start"] = now
        counters["sent"] = 0
    counters["sent"] += delta
    save_config(cfg)


def _remaining_today() -> int:
    counters = _get_apply_counters()
    now = int(time.time())
    if counters["day_start"] == 0 or now - counters["day_start"] >= 24 * 3600:
        return DAILY_APPLY_LIMIT
    return max(0, DAILY_APPLY_LIMIT - int(counters.get("sent", 0) or 0))

def _clean_ids(values: list[str]) -> list[str]:
    """Оставляем только цифры, отбрасываем пустые/NaN/мусор."""
    cleaned: list[str] = []
    for v in values:
        s = str(v or "").strip()
        if not s:
            continue
        m = ID_RE.fullmatch(s)
        if m:
            cleaned.append(m.group(0))
    # Уникализируем с сохранением порядка
    return list(dict.fromkeys(cleaned))


def mass_apply(vacancy_ids: list[str], resume_id: str, message: str | None) -> tuple[int, int, list[str]]:
    ok = 0
    skipped = 0
    errors: list[str] = []

    resume_id = (resume_id or "").strip()
    if not resume_id:
        return (0, len(vacancy_ids), ["Не выбран resume_id"])

    vids = _clean_ids(vacancy_ids)
    if not vids:
        return (0, 0, ["Список вакансий пуст после очистки ID"])

    remaining = _remaining_today()
    if remaining <= 0:
        return (0, len(vids), ["Достигнут лимит 200 откликов за 24 часа"])

    for vid in vids[:remaining]:
        try:
            negotiations.create_response(vid, resume_id, message=message)
            ok += 1
        except Exception as e:
            skipped += 1
            errors.append(f"{vid}: {e}")

    if ok:
        _bump_apply_counters(ok)
    return (ok, skipped + max(0, len(vids) - remaining), errors)

def _get_cleanup_state() -> dict:
    cfg = load_config()
    return cfg.setdefault("cleanup", {"hidden_negotiations": [], "employer_blacklist": []})


def _hide_negotiation_locally(neg_id: str) -> None:
    cfg = load_config()
    stt = cfg.setdefault("cleanup", {"hidden_negotiations": [], "employer_blacklist": []})
    lst = stt.setdefault("hidden_negotiations", [])
    if neg_id not in lst:
        lst.append(neg_id)
        save_config(cfg)


def _blacklist_employer(emp_id: str) -> None:
    cfg = load_config()
    stt = cfg.setdefault("cleanup", {"hidden_negotiations": [], "employer_blacklist": []})
    bl = stt.setdefault("employer_blacklist", [])
    if emp_id and emp_id not in bl:
        bl.append(emp_id)
        save_config(cfg)


def cleanup_rejections() -> tuple[int, list[str]]:
    """
    «Чистим» отказы локально: заносим переговоры в список скрытых,
    а работодателей — опционально в blacklist.
    Возвращает: (сколько помечено, ошибки[])
    """
    from hhcli.api import negotiations  # импорт локально, чтобы избежать циклов

    removed = 0
    errs: list[str] = []

    page = 0
    while True:
        data: dict[str, Any] = negotiations.list_negotiations(page=page, per_page=50) or {}
        items = data.get("items", []) or []
        if not items:
            break

        for it in items:
            # Вычислим «отказанное/закрытое» состояние
            states: list[str] = []
            for key in ("status", "state", "manager_state", "employer_state", "applicant_state"):
                val = it.get(key)
                if isinstance(val, dict):
                    states.append((val.get("id") or "").lower())
                elif isinstance(val, str):
                    states.append(val.lower())
            flat = " ".join(states)
            is_rejected = any(
                s in flat for s in ("discard", "declin", "reject", "refuse", "closed")
            )

            if is_rejected:
                nid = it.get("id") or (
                    it.get("url", "").rsplit("/", 1)[-1] if it.get("url") else ""
                )
                if nid:
                    try:
                        _hide_negotiation_locally(nid)
                        # по желанию: заносим работодателя в чёрный список
                        emp = it.get("employer") or {}
                        emp_id = str(emp.get("id") or "") if isinstance(emp, dict) else ""
                        if emp_id:
                            _blacklist_employer(emp_id)
                        removed += 1
                    except Exception as e:
                        errs.append(f"{nid}: {e}")

        pages = data.get("pages")
        if pages is not None and isinstance(pages, int) and page + 1 >= pages:
            break
        if not items and pages is None:
            break
        page += 1

    return removed, errs


def respond_ui():
    st.subheader("Отклик на вакансию")

    st.caption(
        "Для отклика нужен авторизованный доступ со scope: **read+resumes+negotiations**. "
        "Возьми `vacancy_id` из таблицы поиска, `resume_id` — из «Показать мои резюме» "
        "или из «Проверить доступные резюме»."
    )

    # ========== ОДИНОЧНЫЙ ОТКЛИК ==========
    st.markdown("#### Одиночный отклик")

    vacancy_id = st.text_input("ID вакансии", placeholder="например: 123456789")

    selected_resume_id = ""
    choices = st.session_state.get("respond_resume_choices") or []
    resume_title_map: dict[str, str] = {}

    if not choices:
        # попробуем показать все мои резюме с названием
        try:
            mine = resumes.my_resumes() or {}
            resume_items = (mine.get("items") or []) if isinstance(mine, dict) else []
            for r in resume_items:
                rid = r.get("id")
                if rid:
                    resume_title_map[f"{r.get('title', '(без названия)')} — {rid}"] = rid
        except Exception as err:
            st.error(f"Ошибка загрузки моих резюме: {err}")

    if resume_title_map:
        resume_label = st.selectbox(
            "Выбери резюме для отклика",
            [""] + list(resume_title_map.keys()),
            index=0,
            key="sel_resume_single",
        )
        selected_resume_id = resume_title_map.get(resume_label, "")
    else:
        # fallback — просто id-список
        selected_resume_id = st.selectbox(
            "Выбери резюме для отклика",
            [""] + choices,
            index=0,
            key="sel_resume_single_raw",
        )

    single_message = st.text_area(
        "Сообщение работодателю (необязательно)",
        placeholder="Коротко представьтесь и укажите релевантный опыт",
    )

    col1, col2 = st.columns([1, 1])
    with col1:
        if st.button("Откликнуться ▶"):
            v = (vacancy_id or "").strip()
            r = (selected_resume_id or "").strip()
            if not v:
                st.warning("Укажите ID вакансии.")
            elif not r:
                st.warning("Выберите резюме для отклика.")
            elif _remaining_today() <= 0:
                st.warning("Достигнут лимит 200 откликов за 24 часа.")
            else:
                with st.spinner("Отправляю отклик…"):
                    try:
                        negotiations.create_response(
                            vacancy_id=v,
                            resume_id=r,
                            message=(single_message or "").strip() or None,
                        )
                        _bump_apply_counters(1)
                        st.success("Отклик отправлен.")
                    except Exception as err:
                        # красивый вывод для HTTP ошибок
                        if isinstance(err, requests.HTTPError) and err.response is not None:
                            st.error(f"HTTP {err.response.status_code}: {err.response.text}")
                        else:
                            st.error(f"Ошибка: {err}")

    with col2:
        if st.button("Обновить список моих резюме"):
            try:
                mine = resumes.my_resumes() or {}
                resume_items = (mine.get("items") or []) if isinstance(mine, dict) else []
                st.session_state["respond_resume_choices"] = [
                    i.get("id", "") for i in resume_items if i.get("id")
                ]
                st.success(
                    f"Найдено моих резюме: {len(st.session_state['respond_resume_choices'])}"
                )
            except Exception as e:
                st.error(f"Ошибка загрузки моих резюме: {e}")

    # красивый селект: показываем title и id
    choices = st.session_state.get("respond_resume_choices") or []
    resume_title_map = {}
    if not choices:
        # попробуем показать все мои резюме с названием
        try:
            mine = resumes.my_resumes() or {}
            resume_items = (mine.get("items") or []) if isinstance(mine, dict) else []
            for r in resume_items:
                rid = r.get("id")
                if rid:
                    resume_title_map[f"{r.get('title','(без названия)')} — {rid}"] = rid
        except Exception:
            pass
    if resume_title_map:
        resume_label = st.selectbox(
            "Выбери резюме для отклика",
            [""] + list(resume_title_map.keys()),
            index=0,
            key="sel_resume_single",
        )
        resume_id = resume_title_map.get(resume_label, "")
    else:
        # fallback — просто id-список
        resume_id = st.selectbox(
            "Выбери резюме для отклика", [""] + choices, index=0, key="sel_resume_single_raw"
        )

    message = st.text_area(
        "Сообщение работодателю (необязательно)",
        placeholder="Коротко представьтесь и укажите релевантный опыт",
    )

    colx, coly = st.columns([1, 1])
    with colx:
        st.metric("Лимит откликов / 24ч", 200)
    with coly:
        st.metric("Доступно сейчас", _remaining_today())

    if st.button("Откликнуться ▶"):
        if not vacancy_id:
            st.warning("Укажите ID вакансии.")
            return
        if not resume_id:
            st.warning("Выберите резюме для отклика.")
            return
        if _remaining_today() <= 0:
            st.warning("Достигнут лимит 200 откликов за 24 часа.")
            return
        with st.spinner("Отправляю отклик…"):
            try:
                resp = negotiations.create_response(
                    vacancy_id=vacancy_id, resume_id=resume_id, message=message or None
                )
                # учтём лимит
                _bump_apply_counters(1)
                st.success("Отклик отправлен.")
                if resp:
                    st.json(resp)
            except Exception as err:
                try:
                    import requests  # noqa: WPS433

                    if isinstance(err, requests.HTTPError) and err.response is not None:
                        st.error(f"HTTP {err.response.status_code}: {err.response.text}")
                    else:
                        st.error(f"Ошибка: {err}")
                except Exception:
                    st.error(f"Ошибка: {err}")

    st.divider()

    # ========== МАССОВЫЙ ОТКЛИК ==========
    st.markdown("#### Массовый отклик по найденным вакансиям")

    st.caption(
        "Можно откликаться по результатам поиска (если вы используете вкладку «Поиск») "
        "или вставить список ID вручную (по одному ID на строку)."
    )

    # источник ID: из последнего поиска (если вы их сохраняете в session_state) + ручной ввод
    last_ids: list[str] = st.session_state.get("last_search_ids", [])
    if last_ids:
        st.info(f"Из последнего поиска найдено {len(last_ids)} вакансий.")
    manual_ids_text = st.text_area(
        "Список ID (по одному на строку)", value="", height=120, placeholder="123456\n987654\n..."
    )

    manual_ids = [x.strip() for x in manual_ids_text.splitlines() if x.strip()]
    all_ids_raw = (st.session_state.get("last_search_ids") or []) + manual_ids
    all_ids = _clean_ids(all_ids_raw)  # ← используем очистку

    st.caption(f"Кандидатов после фильтрации ID: {len(all_ids)}")
    if not all_ids:
        st.info("Нет валидных ID вакансий для отклика.")

    # выбор резюме (повторим селект, чтобы не скроллить)
    resume_id_mass = ""
    try:
        mine = resumes.my_resumes() or {}
        resume_items = (mine.get("items") or []) if isinstance(mine, dict) else []
        options = {
            f"{r.get('title','(без названия)')} — {r.get('id','')}": r.get("id", "")
            for r in resume_items
            if r.get("id")
        }
        if options:
            label = st.selectbox(
                "Резюме для массового отклика", list(options.keys()), key="sel_resume_mass"
            )
            resume_id_mass = options.get(label, "")
        else:
            st.warning("У вас нет резюме — массовый отклик недоступен.")
    except Exception as e:
        st.error(f"Не удалось получить резюме: {e}")

    reply_msg = st.text_area(
        "Сообщение (опционально) для массового отклика",
        value="Здравствуйте! Откликаюсь на вашу вакансию. Буду рад обсудить детали.",
        height=100,
    )

    colA, colB, colC = st.columns(3)
    with colA:
        st.metric("Доступно сейчас", _remaining_today())
    with colB:
        st.metric("Кандидатов", len(all_ids))
    with colC:
        max_to_send = st.number_input(
            "Сколько отправить (<= доступно)",
            min_value=0,
            max_value=min(_remaining_today(), len(all_ids)),
            value=min(20, _remaining_today(), len(all_ids)),
            step=1,
        )

    run_apply = st.button(
        "Откликнуться на список ▶",
        type="primary",
        disabled=not (resume_id_mass and all_ids and _remaining_today() > 0),
    )

    if run_apply:
        if not resume_id_mass.strip():
            st.warning("Выберите резюме для отклика.")
        elif not all_ids:
            st.warning("Список ID пуст.")
        else:
            with st.spinner("Отправляем отклики..."):
                ok, skipped, errs = mass_apply(all_ids, resume_id_mass, reply_msg.strip() or None)
                st.success(f"Готово. Успешно: {ok}, пропущено: {skipped}.")
                if errs:
                    st.warning("Некоторые вакансии пропущены/ошибки:")
                    st.code("\n".join(errs)[:8000], language="text")

    st.divider()

    # ========== ЧИСТКА ОТКАЗОВ ==========
    st.markdown("#### Чистка отказов и выход из переписок")
    st.caption(
        "Скрывает отклики с отказами локально (без удаления на стороне hh.ru). Работодатели из таких откликов добавляются в blacklist (по желанию), чтобы не попадались в выдаче и массовых откликах."
    )
    if st.button("Удалить переписки с отказами 🧹"):
        with st.spinner("Чистим..."):
            removed, errs = cleanup_rejections()
            st.success(f"Помечено/скрыто: {removed}")
            if errs:
                st.warning("Некоторые элементы не удалось обработать:")
                for e in errs[:10]:
                    st.write(f"• {e}")


# ========================= Main =========================


def main():
    st.title("HH.ru — Web Search (Streamlit)")

    with st.sidebar:
        st.header("Фильтры")
        text = st.text_input(
            "Поисковая строка", value="Python", placeholder="например: Backend, Java, Data Engineer"
        )
        area_id = area_picker("Локация")

        # Роли
        all_roles = get_roles_cache()
        role_names = [f"{r['name']} ({r['id']})" for r in all_roles]
        selected_roles = st.multiselect("Professional roles", role_names, default=[])
        role_ids = (
            [int(name.split("(")[-1].rstrip(")")) for name in selected_roles]
            if selected_roles
            else None
        )

        # Опыт
        exp_map = {
            "": None,
            "Нет опыта (noExperience)": "noExperience",
            "1–3 года (between1And3)": "between1And3",
            "3–6 лет (between3And6)": "between3And6",
            "6+ лет (moreThan6)": "moreThan6",
        }
        exp_label = st.selectbox("Опыт", list(exp_map.keys()), index=0)
        experience = exp_map[exp_label]

        # Занятость (employment)
        emp_options = ["full", "part", "project", "volunteer", "probation"]
        emp_selected = st.multiselect("Занятость (employment)", emp_options, default=[])

        # Расписание (schedule) — как было
        schedules = get_schedules_cache()
        sched_map: dict[str, str | None] = {"": None}
        sched_map.update({f"{s['name']} ({s['id']})": s["id"] for s in schedules})
        sched_label = st.selectbox("Schedule", list(sched_map.keys()), index=0)
        schedule_id = sched_map[sched_label]

        # Зарплата + валюта
        salary = st.number_input("Зарплата от", min_value=0, step=5000, value=0)
        currency = st.selectbox("Валюта", ["", "RUR", "USD", "EUR"], index=0)
        only_with_salary = st.checkbox("Только с зарплатой", value=False)

        # Поля поиска/сортировка
        search_field = st.selectbox(
            "Искать в поле", ["", "name", "company_name", "description"], index=0
        )
        order_by = st.selectbox("Сортировка", ["", "publication_time", "relevance"], index=0)

        # Даты
        col_d1, col_d2 = st.columns(2)
        with col_d1:
            date_from = st.date_input(
                "Дата с", value=None, format="YYYY-MM-DD"
            )  # returns date|None
        with col_d2:
            date_to = st.date_input("Дата по", value=None, format="YYYY-MM-DD")

        with_address = st.checkbox("Только с адресом", value=False)

        per_page = st.slider("Per page (до 100)", min_value=10, max_value=100, value=50, step=10)
        limit = st.number_input(
            "Максимум вакансий", min_value=10, max_value=5000, value=500, step=50
        )
        include_details = st.checkbox("Включить подробности (медленнее)", value=False)
        run = st.button("Искать ▶")
        st.markdown("### Ключевые слова по полям")

        # Группа INCLUDE
        with st.expander("Искать эти слова (INCLUDE)", expanded=False):
            name_inc = st.text_input("NAME (через запятую)", value="")
            company_inc = st.text_input("COMPANY_NAME (через запятую)", value="")
            desc_inc = st.text_input("DESCRIPTION (через запятую)", value="")

        # Группа EXCLUDE
        with st.expander("Исключить эти слова (EXCLUDE)", expanded=False):
            name_exc = st.text_input("NAME — исключить (через запятую)", value="")
            company_exc = st.text_input("COMPANY_NAME — исключить (через запятую)", value="")
            desc_exc = st.text_input("DESCRIPTION — исключить (через запятую)", value="")

        kw_mode = st.radio(
            "Логика для INCLUDE-блоков", options=["or", "and"], index=0, horizontal=True
        )

        def parse_csv(s: str) -> list[str] | None:
            vals = [x.strip() for x in s.split(",")] if s else []
            vals = [v for v in vals if v]
            return vals or None

        name_kw_list = parse_csv(name_inc)
        company_kw_list = parse_csv(company_inc)
        desc_kw_list = parse_csv(desc_inc)

        name_not_list = parse_csv(name_exc)
        company_not_list = parse_csv(company_exc)
        desc_not_list = parse_csv(desc_exc)

    with st.expander("Вход в hh.ru (OAuth)", expanded=False):
        oauth_ui()

    if run:
        with st.spinner("Выполняю поиск…"):
            from hhcli.utils import build_text_query

            text_built = build_text_query(
                name_kw=name_kw_list,
                name_not=name_not_list,
                company_kw=company_kw_list,
                company_not=company_not_list,
                desc_kw=desc_kw_list,
                desc_not=desc_not_list,
                mode=kw_mode,
            )
            effective_text = text_built or text

            if text_built:
                st.caption("Собранный текст запроса:")
                st.code(text_built, language="text")

            df = search_dataframe(
                text=effective_text,
                area=area_id,
                roles=role_ids,
                schedule=schedule_id,
                per_page=per_page,
                limit=int(limit) if limit else None,
                experience=experience,
                employment=emp_selected or None,
                salary=int(salary) if salary else None,
                currency=currency or None,
                only_with_salary=only_with_salary,
                search_field=search_field or None,
                order_by=order_by or None,
                date_from=str(date_from) if date_from else None,
                date_to=str(date_to) if date_to else None,
                with_address=with_address,
                include_details=include_details,
            )
            if text_built:
                st.caption("Собранный запрос:")
                st.code(text_built, language="text")
        st.success(f"Найдено строк: {len(df)}")

        _cleanup = _get_cleanup_state()
        bl = set(str(x) for x in (_cleanup.get("employer_blacklist") or []))
        if not df.empty and "employer_id" in df.columns and bl:
            df = df[~df["employer_id"].astype(str).isin(bl)].reset_index(drop=True)

        if not df.empty:
            st.dataframe(df, uwidth="stretch", hide_index=True)

            if not df.empty and "id" in df.columns:
                st.session_state["last_search_ids"] = _clean_ids([str(x) for x in df["id"].tolist()])

            fmt = st.selectbox(
                "Формат выгрузки",
                ["CSV", "JSONL", "Parquet"],
                index=0,
                help="Parquet требует pyarrow",
            )
            data_bytes, mime, name = df_to_download(df, fmt)
            if data_bytes:
                st.download_button("Скачать", data=data_bytes, file_name=name, mime=mime)

            st.info(
                "Подсказка: скопируй `id` вакансии из первой колонки и используй ниже в секции «Отклик на вакансию»."
            )
        else:
            st.info("Ничего не найдено. Измени фильтры и попробуй снова.")

    # Секция отклика (всегда доступна, т.к. может потребоваться отдельно от поиска)
    with st.expander("Отклик на вакансию", expanded=False):
        respond_ui()


if __name__ == "__main__":
    main()
