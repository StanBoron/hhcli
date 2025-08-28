from __future__ import annotations

import io
from typing import Any

import pandas as pd
import streamlit as st

from hhcli.api import areas as areas_api
from hhcli.api import dictionaries, professional_roles, resumes, vacancies
from hhcli.auth import build_oauth_url, exchange_code
from hhcli.config import load_config, save_config
from hhcli.http import request  # для /me
from hhcli.utils import format_salary, paginate_vacancies

st.set_page_config(page_title="HH.ru Search", layout="wide")

# ========================= Caching of dictionaries =========================


@st.cache_data(show_spinner=False)
def get_roles_cache() -> list[dict[str, Any]]:
    data = professional_roles.get_roles()
    roles_flat = []
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
) -> pd.DataFrame:
    def fetch(page: int, per_page_: int):
        return vacancies.search_vacancies(
            text=text or "",
            area=area,
            professional_role=roles if roles else None,
            schedule=schedule,
            per_page=per_page_,
            page=page,
        )

    rows = []
    for v in paginate_vacancies(fetch, per_page=per_page, limit=limit):
        salary_str = format_salary(v.get("salary"))
        emp = (v.get("employer") or {}).get("name", "")
        area_name = (v.get("area") or {}).get("name", "")
        rows.append(
            {
                "id": v.get("id", ""),
                "title": v.get("name", ""),
                "employer": emp,
                "salary": salary_str,
                "area": area_name,
                "published_at": v.get("published_at", ""),
                "url": v.get("alternate_url", ""),
            }
        )
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
        # каждая строка — JSON-объект
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

    if st.button("Сгенерировать ссылку на авторизацию"):
        try:
            auth_url = build_oauth_url()
            st.link_button("Перейти к авторизации на hh.ru", url=auth_url, type="primary")
            st.code(auth_url, language="text")
        except Exception as e:
            st.error(f"Не удалось сформировать ссылку: {e}")

    # Захват ?code=... из query-параметров (новое/старое API Streamlit)
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
        schedules = get_schedules_cache()
        sched_map = {"": None}
        sched_map.update({f"{s['name']} ({s['id']})": s["id"] for s in schedules})
        sched_label = st.selectbox("Schedule", list(sched_map.keys()), index=0)
        schedule_id = sched_map[sched_label]
        per_page = st.slider("Per page (до 100)", min_value=10, max_value=100, value=50, step=10)
        limit = st.number_input(
            "Максимум вакансий", min_value=10, max_value=5000, value=500, step=50
        )
        run = st.button("Искать ▶")

    with st.expander("Вход в hh.ru (OAuth)"):
        oauth_ui()

    if run:
        with st.spinner("Выполняю поиск…"):
            df = search_dataframe(
                text=text,
                area=area_id,
                roles=role_ids,
                schedule=schedule_id,
                per_page=per_page,
                limit=int(limit) if limit else None,
            )
        st.success(f"Найдено строк: {len(df)}")

        if not df.empty:
            st.dataframe(df, use_container_width=True, hide_index=True)

            fmt = st.selectbox(
                "Формат выгрузки",
                ["CSV", "JSONL", "Parquet"],
                index=0,
                help="Parquet требует pyarrow",
            )
            data_bytes, mime, name = df_to_download(df, fmt)
            if data_bytes:
                st.download_button("Скачать", data=data_bytes, file_name=name, mime=mime)
        else:
            st.info("Ничего не найдено. Измени фильтры и попробуй снова.")


if __name__ == "__main__":
    main()
