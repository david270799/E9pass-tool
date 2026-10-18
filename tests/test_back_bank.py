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


def test_tilted_table_rows_and_wrapped_road_name():
    from e9pass.ocr.back import parse_back
    lines = ["체류지(Address)", "신고일 체류지", "2022.08.03 경상남도 김해시 진례면 코모로341", "번길 47",
             "2024.05.23 경상북도 영천시 금호읍 원제2길", "16-8,101동1514호(문성모닝타운)", "2025.07 18 김해"]
    assert parse_back(lines)[0] == "경상북도 영천시 금호읍 원제2길 16-8, 101동 1514호 (문성모닝타운)"
    lines = ["2024.03.20 신고일 경상남도 밀양시 하남읍 시동3리2 체류지 KARIMOV ASILBEK BAHT", "길9-1.504호"]
    assert parse_back(lines)[0] == "경상남도 밀양시 하남읍 시동3리2길 9-1, 504호"


def test_address_spacing_restored():
    from e9pass.ocr.back import format_address
    assert format_address("경상남도밀양시하남읍시동3리2길9-1,504호") == "경상남도 밀양시 하남읍 시동3리2길 9-1, 504호"
    assert format_address("대구광역시달서구 월배로 12") == "대구광역시 달서구 월배로 12"
    assert format_address("전라남도진도군 진도읍 남문길 3") == "전라남도 진도군 진도읍 남문길 3"
    assert format_address("경상남도 김해시 진례면 코모로341번길 47") == "경상남도 김해시 진례면 코모로341번길 47"


def test_group_lines_on_tilted_photo():
    from e9pass.ocr.ocr_engine import group_lines
    import math
    def box(x, y, w, h, a):  # наклоненная рамка
        c, s = math.cos(a), math.sin(a)
        return [(x, y), (x + w * c, y + w * s), (x + w * c - h * s, y + w * s + h * c), (x - h * s, y + h * c)]
    a = 0.06  # ~3.5°
    res = [(box(10, 100, 200, 30, a), "2024.05.23", 1), (box(260, 100 + 250 * math.sin(a), 500, 30, a), "경상북도", 1),
           (box(10, 145, 200, 30, a), "2025.07.18", 1), (box(260, 160 + 250 * math.sin(a), 400, 30, a), "16-8", 1),
           (box(900, 50, 30, 400, 0), "KARIMOV ASILBEK", 1)]
    assert group_lines(res) == ["2024.05.23 경상북도", "2025.07.18 16-8"]


def test_address_above_its_centered_date_and_handwritten_newer_row():
    from e9pass.ocr.back import parse_back
    lines = ["일련번호1-022-013-6006", "·체류기간(Duration of Stay)", "2022.05.16 2025.05.16 전주",
             "2025 05.07 202703.16 전주", "·체류지(Address)", "신고일 체류지",
             "전라북도 완주군 봉동읍 원둔산1길", "2022.05.25 21,진솔빌 304호", "2023.5.23", "Et1314210n/202"]
    addr, warnings = parse_back(lines)
    assert addr == "전라북도 완주군 봉동읍 원둔산1길 21, 진솔빌 304호"
    assert warnings and "2023.5.23" in warnings[0]


def test_orientation_prefers_ascending_address_dates():
    from e9pass.ocr.back import orientation_score
    good = ["체류지(Address)", "2022.08.03 경상남도 김해시 진례면 코모로341번길 47",
            "2024.05.23 경상북도 영천시 금호읍 원제2길 16-8"]
    reversed_ = list(reversed(good))
    assert orientation_score(good) > orientation_score(reversed_)
