"""Мост для расширения Chrome: отвечает только расширению E9pay, данные формы KICA."""

import json
import urllib.error
import urllib.request

import pytest

from e9pass import browser_bridge
from e9pass.db import Database


@pytest.fixture(scope="module")
def bridge(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("bridge")
    db = Database(tmp / "db.sqlite3")
    cid = db.add_client("2026-10", full_name="EGAMBERDIEV AZAM- AT PULATOVICH", arc_number="860511-5710023",
                        nationality="UZBEKISTAN", registered_address="경상남도 김해시", phone="01012345678",
                        email="Azamat@Gmail.com", folder_path=str(tmp))
    port = browser_bridge.start(db, get_period=lambda: "2026-10", get_temp=lambda: "112233")
    yield port, cid
    browser_bridge.stop()


def get(port, path, headers):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, None


def test_only_extension_is_allowed(bridge):
    port, _ = bridge
    assert get(port, "/clients", {})[0] == 403                                    # без заголовка
    assert get(port, "/clients", {"Origin": "https://evil.example"})[0] == 403    # чужой сайт
    assert get(port, "/clients", {"Origin": "https://evil.example", "X-E9pay": "1"})[0] == 403
    code, data = get(port, "/clients", {"Origin": f"chrome-extension://{browser_bridge.EXTENSION_ID}"})
    assert code == 200 and data["clients"][0]["arc"] == "860511-5710023"


def test_form_data_for_kica(bridge):
    port, cid = bridge
    code, d = get(port, f"/client/{cid}", {"X-E9pay": "1"})
    assert code == 200
    assert d["name"] == "egamberdiev azamat pulatovich"          # строчными, перенос склеен
    assert (d["rrn1"], d["rrn2"]) == ("860511", "5710023")
    assert d["temp_number"] == "112233"
    assert d["phone"] == ["010", "1234", "5678"]
    assert d["country_ko"] == "우즈베키스탄"
    assert (d["email_local"], d["email_domain"]) == ("azamat", "gmail.com")


def test_address_search_keywords():
    from e9pass.browser_bridge import address_search
    assert address_search("경기도 화성시 향남읍 발안로 45, 101동 202호") == \
        (["발안로 45", "경기도 화성시 향남읍 발안로 45"], "101동 202호")
    assert address_search("대전광역시 중구 산성동 334-11")[0][0] == "산성동 334-11"
    kw, detail = address_search("경기도 수원시 팔달구 효원로 123 101동 202호")
    assert kw[0] == "효원로 123" and "101동 202" not in kw and detail == "101동 202호"
