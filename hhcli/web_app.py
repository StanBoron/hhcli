from __future__ import annotations

import io
import json
import logging
import re
import time
from typing import Any

import pandas as pd
import streamlit as st

from hhcli.api import areas as areas_api
from hhcli.api import dictionaries, negotiations, professional_roles, resumes, vacancies
from hhcli.auth import build_oauth_url, exchange_code, set_tokens
from hhcli.config import load_config, save_config
from hhcli.diag import runtime_snapshot
from hhcli.http import request
from hhcli.logs import setup_logging
from hhcli.utils import build_text_query, format_salary, paginate_vacancies

# ------------------------ ЛОГИ ------------------------
log_file = setup_logging()  # читает HHCLI_LOG_LEVEL/HHCLI_LOG_FILE
log = logging.getLogger("hhcli.webapp")
log.info("WebApp start; log_file=%s", log_file)
log.debug("Runtime snapshot: %s", runtime_snapshot())

# ------------------------ UI -------------------------
st.set_page_config(page_title="HH.ru Search", layout="wide")

ID_RE = re.compile(r"^\d+$")
DAILY_APPLY_LIMIT = 200  # лимит откликов за 24 часа на hh.ru


# ========================= Caching of dictionaries =========================
@st.cache_data(show_spinner=False)
def get_roles_cache() -> list[dict[str, Any]]:
    data = professional_roles.get_roles()
    roles_flat: list[dict[str, Any]] = []
    for group in data.get("categories", []):
        for r in group.get("roles", []):
            roles_flat.append({"id": int(r["id"]), "name": r["name"], "group": group["name"]})
    log.debug("[CACHE] roles: %s items", len(roles_flat))
    return roles_flat


@st.cache_data(show_spinner=False)
def get_schedules_cache() -> list[dict[str, str]]:
    data = dictionaries.get_dictionaries()
    sched = data.get("schedule", []) or []
    out = [{"id": s["id"], "name": s["name"]} for s in sched]
    log.debug("[CACHE] schedules: %s items", len(out))
    return out


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
    include_details: bool = False,
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
            except Exception as e:
                log.debug("[SEARCH] include_details err for id=%s: %s", row["id"], e)
        rows.append(row)

    df = pd.DataFrame(rows)
    log.debug("[SEARCH] DF built rows=%s cols=%s", len(df), list(df.columns))
    return df


def df_to_download(df: pd.DataFrame, fmt: str) -> tuple[bytes | None, str, str]:
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

    if st.button("Сгенерировать ссылку на авторизацию"):
        try:
            auth_url = build_oauth_url()
            st.link_button("Перейти к авторизации на hh.ru", url=auth_url, type="primary")
            st.code(auth_url, language="text")
        except Exception as e:
            st.error(f"Не удалось сформировать ссылку: {e}")

    # Захват ?code=...
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
                token_obj = data.get("token") if isinstance(data, dict) else None
                base = token_obj or data or {}
                access_token = base.get("access_token")
                refresh_token = base.get("refresh_token")

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

    # --- Экспорт токенов в JSON ---
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
        if st.button("Показать мои резюме", key="btn_oauth_show_resumes"):
            try:
                data = resumes.my_resumes()
                st.json(data)
            except Exception as e:
                st.error(f"Ошибка запроса резюме: {e}")


# ========================= Respond (negotiations) =========================
def _get_cleanup_state() -> dict:
    cfg = load_config()
    return cfg.setdefault("cleanup", {"hidden_negotiations": [], "employer_blacklist": []})


def _hide_negotiation_locally(neg_id: str) -> None:
    if not neg_id:
        return
    cfg = load_config()
    stt = cfg.setdefault("cleanup", {"hidden_negotiations": [], "employer_blacklist": []})
    lst: list[str] = stt.setdefault("hidden_negotiations", [])
    if neg_id not in lst:
        lst.append(neg_id)
        save_config(cfg)


def _blacklist_employer(emp_id: str) -> None:
    if not emp_id:
        return
    cfg = load_config()
    stt = cfg.setdefault("cleanup", {"hidden_negotiations": [], "employer_blacklist": []})
    bl: list[str] = stt.setdefault("employer_blacklist", [])
    if emp_id not in bl:
        bl.append(emp_id)
        save_config(cfg)


def cleanup_rejections() -> tuple[int, list[str]]:
    removed = 0
    errs: list[str] = []

    page = 0
    while True:
        data: dict[str, Any] = negotiations.list_negotiations(page=page, per_page=50) or {}
        items = data.get("items", []) or []
        if not items:
            break

        for it in items:
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
                try:
                    if nid:
                        _hide_negotiation_locally(nid)
                    emp = it.get("employer") or {}
                    emp_id = str(emp.get("id") or "") if isinstance(emp, dict) else ""
                    if emp_id:
                        _blacklist_employer(emp_id)
                    removed += 1
                except Exception as err:
                    errs.append(f"{nid or 'unknown'}: {err}")

        pages = data.get("pages")
        if isinstance(pages, int) and page + 1 >= pages:
            break
        if not items and pages is None:
            break
        page += 1

    return removed, errs


def _clean_ids(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    for v in values:
        s = str(v or "").strip()
        if s and ID_RE.fullmatch(s):
            cleaned.append(s)
    return list(dict.fromkeys(cleaned))


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
    counters["sent"] += int(delta)
    save_config(cfg)


def _remaining_today() -> int:
    counters = _get_apply_counters()
    now = int(time.time())
    if counters["day_start"] == 0 or now - counters["day_start"] >= 24 * 3600:
        return DAILY_APPLY_LIMIT
    sent = int(counters.get("sent", 0) or 0)
    return max(0, DAILY_APPLY_LIMIT - sent)


def mass_apply(
    vacancy_ids: list[str], resume_id: str, message: str | None
) -> tuple[int, int, list[str]]:
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

    to_send = vids[:remaining]
    log.debug("[RESPOND_MASS] will_send=%s of %s", len(to_send), len(vids))

    for vid in to_send:
        try:
            payload_preview = {"vacancy_id": vid, "resume_id": resume_id}
            if message:
                payload_preview["message"] = {"text": message}
            log.debug("[RESPOND_MASS] payload=%s", payload_preview)

            negotiations.create_response(vid, resume_id, message=message)
            ok += 1
        except Exception as err:
            skipped += 1
            errors.append(f"{vid}: {err}")
            log.exception("[RESPOND_MASS] error vid=%s err=%s", vid, err)

    if ok:
        _bump_apply_counters(ok)

    skipped += max(0, len(vids) - len(to_send))
    return (ok, skipped, errors)


# ======== UI: одиночный и массовый отклики ========
def respond_ui() -> None:
    st.subheader("Отклик на вакансию")
    log.debug("[RESPOND_UI] open")

    st.caption(
        "Для отклика нужен авторизованный доступ со scope: **read + resumes + negotiations**. "
        "ID вакансии берётся из поиска, ID резюме — из «Показать мои резюме» или «Проверить доступные резюме для вакансии»."
    )

    # ---------- Одиночный отклик ----------
    st.markdown("### Одиночный отклик")

    vacancy_id = st.text_input("ID вакансии", placeholder="например: 123456789")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Проверить доступные резюме для вакансии", key="btn_check_resumes_for_vac"):
            v = (vacancy_id or "").strip()
            if not v:
                st.warning("Укажите ID вакансии.")
            else:
                try:
                    data = negotiations.vacancy_resumes(v)
                    items = data.get("items", []) or []
                    if not items:
                        st.info(
                            "Подходящих резюме не найдено (или нет права отклика по этой вакансии)."
                        )
                    else:
                        st.session_state["respond_resume_choices"] = [
                            i.get("id", "") for i in items if i.get("id")
                        ]
                        st.success(
                            f"Доступных резюме: {len(st.session_state['respond_resume_choices'])}"
                        )
                except Exception as err:
                    st.error(f"Ошибка проверки резюме: {err}")

    with col2:
        if st.button("Показать мои резюме", key="btn_show_my_resumes_single"):
            try:
                mine = resumes.my_resumes() or {}
                resume_items = (mine.get("items") or []) if isinstance(mine, dict) else []
                st.session_state["respond_resume_choices"] = [
                    i.get("id", "") for i in resume_items if i.get("id")
                ]
                st.success(
                    f"Найдено моих резюме: {len(st.session_state['respond_resume_choices'])}"
                )
            except Exception as err:
                st.error(f"Ошибка загрузки моих резюме: {err}")

    selected_resume_id = ""
    choices = st.session_state.get("respond_resume_choices") or []
    resume_title_map: dict[str, str] = {}

    if not choices:
        try:
            mine = resumes.my_resumes() or {}
            resume_items = (mine.get("items") or []) if isinstance(mine, dict) else []
            for r in resume_items:
                rid = r.get("id")
                if rid:
                    label = f"{r.get('title','(без названия)')} — {rid}"
                    resume_title_map[label] = rid
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

    colx, coly = st.columns(2)
    with colx:
        st.metric("Лимит откликов / 24ч", DAILY_APPLY_LIMIT)
    with coly:
        st.metric("Доступно сейчас", _remaining_today())

    if st.button("Откликнуться ▶", key="btn_single_apply"):
        v = (vacancy_id or "").strip()
        r = (selected_resume_id or "").strip()
        msg = (single_message or "").strip() or None

        log.debug(
            "[RESPOND_SINGLE] inputs vacancy_id=%r resume_id=%r msg_len=%s",
            v,
            r,
            (len(msg) if msg else 0),
        )

        if not v:
            st.warning("Укажите ID вакансии.")
            log.warning("[RESPOND_SINGLE] no vacancy_id")
            return
        if not r:
            st.warning("Выберите резюме для отклика.")
            log.warning("[RESPOND_SINGLE] no resume_id")
            return
        if _remaining_today() <= 0:
            st.warning("Достигнут лимит 200 откликов за 24 часа.")
            log.warning("[RESPOND_SINGLE] apply limit reached")
            return

        payload = {"vacancy_id": v, "resume_id": r, "message": {"text": msg} if msg else None}
        log.debug("[RESPOND_SINGLE] payload=%s", payload)

        with st.spinner("Отправляю отклик…"):
            try:
                resp = negotiations.create_response(vacancy_id=v, resume_id=r, message=msg)
                log.debug("[RESPOND_SINGLE] response=%s", (resp if resp else "<no body>"))
                _bump_apply_counters(1)
                st.success("Отклик отправлен.")
            except Exception as err:
                log.exception("[RESPOND_SINGLE] FAILED err=%s", err)
                import requests as _rq  # noqa: WPS433

                if isinstance(err, _rq.HTTPError) and getattr(err, "response", None) is not None:
                    st.error(f"HTTP {err.response.status_code}: {err.response.text}")
                else:
                    st.error(f"Ошибка: {err}")

    st.divider()

    # ---------- Массовый отклик ----------
    st.markdown("### Массовый отклик")

    st.caption(
        "Можно откликаться по результатам поиска (если в другой вкладке уже сделали поиск) "
        "— ID возьмём из `st.session_state['last_search_ids']` — или вставить список ID вручную (по одному на строку)."
    )

    last_ids: list[str] = st.session_state.get("last_search_ids", []) or []
    if last_ids:
        st.info(f"Из последнего поиска найдено {len(last_ids)} вакансий.")

    manual_ids_text = st.text_area(
        "Список ID (по одному на строку)",
        value="",
        height=120,
        placeholder="123456\n987654\n...",
    )
    manual_ids = [x.strip() for x in manual_ids_text.splitlines() if x.strip()]

    all_ids_raw = last_ids + manual_ids
    all_ids = _clean_ids(all_ids_raw)
    st.caption(f"Кандидатов после фильтрации ID: {len(all_ids)}")
    log.debug("[RESPOND_MASS] merged_ids=%s cleaned=%s", len(all_ids_raw), len(all_ids))

    # резюме для массового отклика
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
                "Резюме для массового отклика", [""] + list(options.keys()), key="sel_resume_mass"
            )
            resume_id_mass = options.get(label, "")
        else:
            st.warning("У вас нет резюме — массовый отклик недоступен.")
    except Exception as err:
        st.error(f"Не удалось получить резюме: {err}")

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
        key="btn_mass_apply",
        type="primary",
        disabled=not (resume_id_mass and all_ids and _remaining_today() > 0 and max_to_send > 0),
    )

    if run_apply:
        r = (resume_id_mass or "").strip()
        vids = all_ids[: int(max_to_send)]
        log.debug(
            "[RESPOND_MASS] resume_id=%r max_to_send=%s vids_first_10=%s count=%s",
            r,
            max_to_send,
            vids[:10],
            len(vids),
        )

        if not r:
            st.warning("Выберите резюме для отклика.")
            log.warning("[RESPOND_MASS] no resume_id")
        elif not vids:
            st.warning("Список ID пуст.")
            log.warning("[RESPOND_MASS] no vacancy_ids")
        else:
            preview = [{"vacancy_id": v, "resume_id": r} for v in vids[:3]]
            log.debug("[RESPOND_MASS] payload_preview=%s", preview)
            with st.expander("DEBUG (первые 3 payloads)", expanded=False):
                st.json(preview)

            with st.spinner("Отправляем отклики..."):
                try:
                    ok, skipped, errs = mass_apply(vids, r, (reply_msg or "").strip() or None)
                    log.debug(
                        "[RESPOND_MASS] result ok=%s skipped=%s err_count=%s errs_head=%s",
                        ok,
                        skipped,
                        len(errs),
                        errs[:3],
                    )
                    st.success(f"Готово. Успешно: {ok}, пропущено: {skipped}.")
                    if errs:
                        st.warning("Некоторые вакансии пропущены/ошибки:")
                        st.code("\n".join(errs)[:8000], language="text")
                except Exception as err:
                    log.exception("[RESPOND_MASS] FAILED err=%s", err)
                    st.error(f"Ошибка массового отклика: {err}")


# ========================= Main =========================
def main():
    st.title("HH.ru — Web Search (Streamlit)")

    with st.sidebar:
        st.header("Фильтры")
        text = st.text_input(
            "Поисковая строка", value="Python", placeholder="например: Backend, Java, Data Engineer"
        )
        area_id = area_picker("Локация")

        all_roles = get_roles_cache()
        role_names = [f"{r['name']} ({r['id']})" for r in all_roles]
        selected_roles = st.multiselect("Professional roles", role_names, default=[])
        role_ids = (
            [int(name.split("(")[-1].rstrip(")")) for name in selected_roles]
            if selected_roles
            else None
        )

        exp_map = {
            "": None,
            "Нет опыта (noExperience)": "noExperience",
            "1–3 года (between1And3)": "between1And3",
            "3–6 лет (between3And6)": "between3And6",
            "6+ лет (moreThan6)": "moreThan6",
        }
        exp_label = st.selectbox("Опыт", list(exp_map.keys()), index=0)
        experience = exp_map[exp_label]

        emp_options = ["full", "part", "project", "volunteer", "probation"]
        emp_selected = st.multiselect("Занятость (employment)", emp_options, default=[])

        schedules = get_schedules_cache()
        sched_map: dict[str, str | None] = {"": None}
        sched_map.update({f"{s['name']} ({s['id']})": s["id"] for s in schedules})
        sched_label = st.selectbox("Schedule", list(sched_map.keys()), index=0)
        schedule_id = sched_map[sched_label]

        salary = st.number_input("Зарплата от", min_value=0, step=5000, value=0)
        currency = st.selectbox("Валюта", ["", "RUR", "USD", "EUR"], index=0)
        only_with_salary = st.checkbox("Только с зарплатой", value=False)

        search_field = st.selectbox(
            "Искать в поле", ["", "name", "company_name", "description"], index=0
        )
        order_by = st.selectbox("Сортировка", ["", "publication_time", "relevance"], index=0)

        col_d1, col_d2 = st.columns(2)
        with col_d1:
            date_from = st.date_input("Дата с", value=None, format="YYYY-MM-DD")
        with col_d2:
            date_to = st.date_input("Дата по", value=None, format="YYYY-MM-DD")

        with_address = st.checkbox("Только с адресом", value=False)

        per_page = st.slider("Per page (до 100)", min_value=10, max_value=100, value=50, step=10)
        limit = st.number_input(
            "Максимум вакансий", min_value=10, max_value=5000, value=500, step=50
        )
        include_details = st.checkbox("Включить подробности (медленнее)", value=False)
        run = st.button("Искать ▶")

        # --- Ключевые слова по полям ---
        st.markdown("### Ключевые слова по полям")

        with st.expander("Искать эти слова (INCLUDE)", expanded=False):
            name_inc = st.text_input("NAME (через запятую)", value="")
            company_inc = st.text_input("COMPANY_NAME (через запятую)", value="")
            desc_inc = st.text_input("DESCRIPTION (через запятую)", value="")

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

            log.debug(
                "[SEARCH] built_text=%r base_text=%r effective_text=%r",
                text_built,
                text,
                effective_text,
            )

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

        st.success(f"Найдено строк: {len(df)}")
        log.debug("[SEARCH] found_rows=%s cols=%s", len(df), list(df.columns))
        if not df.empty and "id" in df.columns:
            ids = [str(x) for x in df["id"].tolist()][:20]
            log.debug("[SEARCH] first_ids=%s", ids)

        _cleanup = _get_cleanup_state()
        bl = set(str(x) for x in (_cleanup.get("employer_blacklist") or []))
        if not df.empty and "employer_id" in df.columns and bl:
            df = df[~df["employer_id"].astype(str).isin(bl)].reset_index(drop=True)

        if not df.empty:
            st.dataframe(df, width="stretch", hide_index=True)

            if "id" in df.columns:
                st.session_state["last_search_ids"] = _clean_ids(
                    [str(x) for x in df["id"].tolist()]
                )

            fmt = st.selectbox("Формат выгрузки", ["CSV", "JSONL", "Parquet"], index=0)
            data_bytes, mime, name = df_to_download(df, fmt)
            if data_bytes:
                st.download_button("Скачать", data=data_bytes, file_name=name, mime=mime)

            st.info(
                "Подсказка: скопируй `id` вакансии из первой колонки и используй ниже в секции «Отклик на вакансию»."
            )
        else:
            st.info("Ничего не найдено. Измени фильтры и попробуй снова.")

    with st.expander("Отклик на вакансию", expanded=False):
        respond_ui()


if __name__ == "__main__":
    main()
