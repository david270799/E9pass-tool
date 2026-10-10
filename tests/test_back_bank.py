from e9pass.ocr.back import parse_back
from e9pass.ocr.bank import parse_bank
from e9pass.ocr.detect import BACK, BANK, FRONT, classify_photo

BACK_LINES = [
    "제루기간 연장히가 Extension of Period 허가일자 만로일자 확인",
    "202405.12 2027,05.11 수왼출입국",
    "체류지 변경신고 Report 신고일 of Change of Address",
    "2023.05.12 경기도 수원시 팔달구 효원로 123 팔달구청장",
    "2024.02.01 경기도 화성시 향남읍 발안로 45, 101동 202호",
    "화성시장",
]


def test_back_takes_last_address_without_stamp():
    addr, warnings = parse_back(BACK_LINES)
    assert addr == "경기도 화성시 향남읍 발안로 45, 101동 202호"
    assert warnings == []


def test_back_falls_back_to_any_readable_address():
    lines = ["체류지", "2023.05.12 인천광역시 남동구 남동대로 215 남동구정장", "2024.0 ##### 흐릿"]
    addr, _ = parse_back(lines)
    assert addr == "인천광역시 남동구 남동대로 215"


def test_back_no_address():
    addr, warnings = parse_back(["체류기간 연장허가", "2024.05.12 2027.05.11"])
    assert addr == "" and warnings


def test_bank_with_ocr_typo_in_name():
    bank, acc, w = parse_bank(["NH농현은행", "보통예금 통장", "계좌번호", "302-1234-5678-91", "예금주"])
    assert bank == "NH농협은행"
    assert acc == "302-1234-5678-91"
    assert w == []


def test_bank_ignores_phone_and_dates():
    bank, acc, _ = parse_bank(["신한은행", "010-1234-5678", "2023.05.20", "계좌번호 110-123-456789"])
    assert bank == "신한은행" and acc == "110-123-456789"


def test_classify_photos():
    assert classify_photo(BACK_LINES)[0] == BACK
    assert classify_photo(["KB국민은행", "계좌번호 123456-01-234567", "통장"])[0] == BANK
    assert classify_photo(["외국인등록증 RESIDENCE CARD", "950101-5123456"])[0] == FRONT
    assert classify_photo(["흐릿한 사진"])[0] == ""


def test_back_stamp_glued_to_next_row():
    lines = ["체류지 변경신고", "2023.05.12 경기도 수원시 팔달구 효원로 123",
             "팔달구정장 2024.02.01 경기도 화성시 향남읍 발안로 45, 101동 202호"]
    assert parse_back(lines)[0] == "경기도 화성시 향남읍 발안로 45, 101동 202호"
