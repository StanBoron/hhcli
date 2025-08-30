# hhcli/http.py
from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Mapping, MutableMapping
from typing import Any, cast

import requests

from hhcli.config import load_config
from hhcli.logs import http_log_request, http_log_response, parse_request_id

log = logging.getLogger("hhcli.http")

API_BASE = "https://api.hh.ru"


def _get_token() -> str:
    cfg = load_config()
    tok = cfg.get("access_token") or ""
    if not tok:
        raise RuntimeError("Нет access_token. Авторизуйтесь в разделе OAuth.")
    return tok


def request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    auth: bool = False,
    timeout: int = 30,
    retries: int = 1,
) -> Any:
    """
    Общая обёртка HTTP. Для POST /negotiations:
      1) шлём JSON с vacancy_id, resume_id, message[text]
      2) если 400 с 'bad_argument' по обоим полям — повторяем как x-www-form-urlencoded
    """
    url = path if path.startswith("http") else f"{API_BASE}{path}"
    extra_headers: MutableMapping[str, str] = {}
    if headers:
        extra_headers.update(headers)
    if auth:
        extra_headers["Authorization"] = f"Bearer {_get_token()}"
    # стандартный UA
    extra_headers.setdefault("User-Agent", "hhcli/streamlit (+https://github.com/...)")

    # лог запроса (не падаем, даже если логгер отвалился)
    with contextlib.suppress(Exception):
        http_log_request(
            method,
            url,
            params=params,
            json_body=json,
            form_body=data,
            headers=dict(extra_headers),
        )

    last_err: Exception | None = None

    for attempt in range(retries):
        t0 = time.time()
        try:
            resp = requests.request(
                method,
                url,
                params=params,
                json=json,
                data=data,
                headers=cast(Mapping[str, str], extra_headers),
                timeout=timeout,
            )
            elapsed_ms = int((time.time() - t0) * 1000)
            rid = parse_request_id(resp)
            with contextlib.suppress(Exception):
                http_log_response(
                    method,
                    url,
                    status=resp.status_code,
                    elapsed_ms=elapsed_ms,
                    text_preview=(resp.text or "")[:500],
                    request_id=rid,
                )
            resp.raise_for_status()
            return resp.json() if resp.text else None

        except requests.HTTPError as err:
            # --- Спец-лог и ретрай для POST /negotiations ---
            need_form_retry = False
            try:
                body = err.response.text if err.response is not None else ""
                status = err.response.status_code if err.response is not None else "?"
                if method.upper() == "POST" and url.endswith("/negotiations") and status == 400:
                    # если сервер не распознал оба аргумента — часто значит, что он не принял JSON
                    # логируем максимально подробно
                    log.error(
                        "Negotiations JSON attempt failed: status=400 body=%s | sent_json=%s | headers=%s",
                        body,
                        json,
                        {
                            k: (v if k.lower() != "authorization" else "***")
                            for k, v in extra_headers.items()
                        },
                    )
                    if body and ("vacancy_id" in body and "resume_id" in body):
                        need_form_retry = True
            except Exception:
                # не мешаем основной логике
                pass

            if need_form_retry:
                # подготовим form-urlencoded
                form: dict[str, Any] = {}
                if isinstance(json, dict):
                    form["vacancy_id"] = json.get("vacancy_id", "")
                    form["resume_id"] = json.get("resume_id", "")
                    msg = json.get("message")
                    if isinstance(msg, dict) and "text" in msg:
                        form["message"] = msg.get("text") or ""

                # отдельный набор заголовков без JSON Content-Type (requests сам поставит form default)
                form_headers = dict(extra_headers)
                # если кто-то принудительно выставил JSON заголовок — уберём его
                for key in list(form_headers.keys()):
                    if key.lower() == "content-type":
                        form_headers.pop(key)

                # подробный DEBUG payload (но без токена)
                log.debug(
                    "Negotiations form retry: data=%s headers=%s",
                    form,
                    {
                        k: (v if k.lower() != "authorization" else "***")
                        for k, v in form_headers.items()
                    },
                )

                t1 = time.time()
                resp2 = requests.request(
                    method,
                    url,
                    params=params,
                    data=form,
                    headers=form_headers,
                    timeout=timeout,
                )
                elapsed_ms2 = int((time.time() - t1) * 1000)
                rid2 = parse_request_id(resp2)
                with contextlib.suppress(Exception):
                    http_log_response(
                        method,
                        url,
                        status=resp2.status_code,
                        elapsed_ms=elapsed_ms2,
                        text_preview=(resp2.text or "")[:500],
                        request_id=rid2,
                    )
                try:
                    resp2.raise_for_status()
                    return resp2.json() if resp2.text else None
                except Exception as e2:
                    last_err = e2
                    if attempt == retries - 1:
                        raise e2
                    continue  # к следующей попытке (если задано >1)

            # обычный путь: не наш случай или не помог ретрай
            last_err = err
            if attempt == retries - 1:
                raise err
            continue

        except Exception as err:
            last_err = err
            if attempt == retries - 1:
                raise err

    # теоретически не дойдём
    if last_err:
        raise last_err
    return None
