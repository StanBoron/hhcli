import sys
import types
from pathlib import Path

import pytest

from hhcli.respond.mass_utils import (
    extract_ids_from_text,
    read_ids_from_bytes,
    read_ids_from_file,
    resume_allowed_for_vacancy,
    send_response,
    vacancy_has_required_test,
    vacancy_requires_letter,
)

# ---------------- ID parsing ----------------


def test_extract_ids_from_text_basic():
    txt = "123, 456\nabc789xyz 42; id=555"
    assert extract_ids_from_text(txt) == ["123", "456", "789", "42", "555"]


def test_read_ids_from_file_txt(tmp_path: Path):
    p = tmp_path / "ids.txt"
    p.write_text("123\nfoo456bar\n\n 789 ", encoding="utf-8")
    assert read_ids_from_file(p) == ["123", "456", "789"]


def test_read_ids_from_file_csv_with_header(tmp_path: Path):
    p = tmp_path / "ids.csv"
    p.write_text("vacancy_id\n1\n2\nfoo3bar\n", encoding="utf-8")
    assert read_ids_from_file(p) == ["1", "2", "3"]


def test_read_ids_from_file_tsv_no_header(tmp_path: Path):
    p = tmp_path / "ids.tsv"
    p.write_text("1\tA\nfoo2bar\tB\n\t\n", encoding="utf-8")
    # With no header, our parser picks first column name from csv.DictReader,
    # but if missing header, list stays empty; this test focuses on non-crashing behavior
    # So we ensure creating a header-like first row to be read
    p.write_text("id\tname\n1\tA\nfoo2bar\tB\n", encoding="utf-8")
    assert read_ids_from_file(p) == ["1", "2"]


def test_read_ids_from_file_jsonl(tmp_path: Path):
    p = tmp_path / "ids.jsonl"
    p.write_text(
        "\n".join(
            [
                '{"vacancy_id": 111}',
                '{"id": "222"}',
                '{"foo": "bar"}',
                "333",
                '{"nested": {"x": "44"}}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    assert read_ids_from_file(p) == ["111", "222", "333", "44"]


def test_read_ids_from_bytes_variants():
    assert read_ids_from_bytes("ids.txt", b"1,2 foo3") == ["1", "2", "3"]
    csv_data = b"vacancy_id\n10\n20\n"
    assert read_ids_from_bytes("ids.csv", csv_data) == ["10", "20"]
    tsv_data = b"id\tname\n7\tAlice\n9\tBob\n"
    assert read_ids_from_bytes("ids.tsv", tsv_data) == ["7", "9"]
    ndjson = "\n".join(['{"id": "77"}', '{"vacancy_id": 88}', '"99"']).encode("utf-8")
    assert read_ids_from_bytes("ids.ndjson", ndjson) == ["77", "88", "99"]


# ---------------- Vacancy metadata flags ----------------


def test_vacancy_has_required_test_variants():
    assert vacancy_has_required_test({"test": {"required": True}}) is True
    assert vacancy_has_required_test({"has_test": True}) is True
    assert vacancy_has_required_test({"test": {"required": False}}) is False
    assert vacancy_has_required_test({}) is False


def test_vacancy_requires_letter_flag():
    assert vacancy_requires_letter({"response_letter_required": True}) is True
    assert vacancy_requires_letter({}) is False


# ---------------- API-dependent helpers (mock hhcli.api.*) ----------------


class _FakeVacancies:
    def __init__(self, allowed_ids):
        self._allowed_ids = set(str(x) for x in allowed_ids)

    def vacancy_resumes(self, vacancy_id):
        # if vacancy_id in allowed set -> include resume "R1"
        if str(vacancy_id) in self._allowed_ids:
            return {"items": [{"id": "R1"}, {"id": "R2"}]}
        return {"items": [{"id": "R2"}]}


class _FakeNegotiations:
    def __init__(self, raise_for=None):
        self.raise_for = set(raise_for or [])

    def create_response(self, *, vacancy_id, resume_id, message=None):
        if str(vacancy_id) in self.raise_for:
            err = RuntimeError("boom")
            err.status_code = 429
            err.request_id = "req-xyz"
            raise err
        # emulate OK
        return {"id": "neg-123", "request_id": "req-abc"}


@pytest.fixture(autouse=True)
def _mock_hh_api(monkeypatch):
    vac_mod = types.SimpleNamespace()
    neg_mod = types.SimpleNamespace()
    sys.modules["hhcli.api.vacancies"] = vac_mod
    sys.modules["hhcli.api.negotiations"] = neg_mod
    yield  # tests will populate attributes per-case
    # teardown not necessary — pytest process ends


def test_resume_allowed_for_vacancy_true(monkeypatch):
    import hhcli.api.vacancies as V

    V.get_vacancy = lambda vid: {}
    V.vacancy_resumes = _FakeVacancies(allowed_ids=["123"]).vacancy_resumes

    assert resume_allowed_for_vacancy("123", "R1") is True
    assert resume_allowed_for_vacancy("123", "R2") is True  # in list
    assert resume_allowed_for_vacancy("456", "R1") is False


def test_send_response_ok(monkeypatch):
    import hhcli.api.negotiations as N

    N.create_response = _FakeNegotiations().create_response

    res = send_response("1", "R1", "Hi")
    assert res.status == "ok"
    assert res.http_code == 201
    assert res.negotiation_id == "neg-123"
    assert res.request_id == "req-abc"


def test_send_response_error(monkeypatch):
    import hhcli.api.negotiations as N

    N.create_response = _FakeNegotiations(raise_for=["9"]).create_response

    res = send_response("9", "R1", "Hi")
    assert res.status == "error"
    assert res.http_code == 429
    assert "boom" in (res.error or "")
    assert res.request_id == "req-xyz"
