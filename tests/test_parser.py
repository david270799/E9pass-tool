from e9pass.ocr.front import find_country, find_reg_no, info_from_reg_no, parse_card

CLEAN = [
    "외국인등록증 RESIDENCE CARD",
    "외국인등록번호 Registration No",
    "950101-5123456",
    "성명 Name",
    "NGUYEN VAN AN",
    "국가 지역 Country/Region",
    "VIETNAM",
    "성별 Sex",
    "M",
    "체류자격 Status",
    "비전문취업(E-9)",
    "발급일자 Issue Date",
    "2023.05.12",
    "수원출입국 외국인사무소장",
]


def test_clean_card():
    d = parse_card(CLEAN)
    assert d.reg_no == "950101-5123456"
    assert d.name == "NGUYEN VAN AN"
    assert d.country == "VIETNAM"
    assert d.country_ru == "Вьетнам"
    assert d.sex == "M"
    assert d.birth_date == "1995-01-01"
    assert d.visa == "E-9"
    assert d.visa_full == "비전문취업(E-9)"
    assert d.issue_date == "2023-05-12"
    assert d.issuer == "수원출입국 외국인사무소장"
    assert d.warnings == []


def test_noisy_ocr():
    lines = [
        "외국인등록증 RESIDENCE CARD",
        "외국인등록번호 95O1O1 - 6I23456",   # буквы вместо цифр
        "성명 Name TURSUNOVA MADINA",          # имя в одной строке с подписью
        "국가/지역 UZBEKISTN",                # опечатка OCR
        "체류자격 비전문취업 (E - 9)",
        "발급일자 2024. 11. 3",
        "서울출입국·외국인청장",
    ]
    d = parse_card(lines)
    assert d.reg_no == "950101-6123456"
    assert d.sex == "F"
    assert d.name == "TURSUNOVA MADINA"
    assert d.country == "UZBEKISTAN"
    assert d.visa == "E-9"
    assert d.issue_date == "2024-11-03"
    assert d.issuer == "서울출입국·외국인청장"


def test_missing_fields_are_flagged():
    d = parse_card(["외국인등록증", "흐릿한 사진"])
    assert "номер не найден" in d.warnings
    assert any("ФИО" in w for w in d.warnings)


def test_reg_no_century_and_sex():
    assert info_from_reg_no("000315-8234567") == ("F", "2000-03-15")
    assert info_from_reg_no("881231-5000000") == ("M", "1988-12-31")
    assert info_from_reg_no("881341-5000000") == ("M", "")  # неверная дата


def test_korean_reg_no_not_matched():
    # 7-я цифра 1-4 — номер гражданина Кореи, не иностранца
    assert find_reg_no(["900101-1234567"]) == ""


def test_country_korean_and_indonesia_vs_india():
    assert find_country(["국적 인도네시아"]) == "INDONESIA"
    assert find_country(["국적 인도"]) == "INDIA"
    assert find_country(["SRI LANKA"]) == "SRI LANKA"


def test_visa_with_subtype():
    d = parse_card(["체류자격 Status 방문취업(H-2)", "F-4-1"])
    assert d.visa == "H-2"


def test_name_label_ocr_typo_is_not_a_name():
    d = parse_card(["성명 Namne", "BATBAYAR GANBOLD", "MONGOLIA", "910722-5345678"])
    assert d.name == "BATBAYAR GANBOLD"


def test_name_wrapped_with_hyphen_is_joined():
    lines = ["성명 Name", "EGAMBERDIEV AZAM-", "AT PULATOVICH", "국가/지역 Country/Region", "UZBEKISTAN"]
    assert parse_card(lines).name == "EGAMBERDIEV AZAMAT PULATOVICH"


def test_name_hyphen_glued_in_one_line():
    from e9pass.ocr.front import normalize_name
    assert normalize_name("EGAMBERDIEV AZAM- AT PULATOVICH") == "EGAMBERDIEV AZAMAT PULATOVICH"
    assert normalize_name("AL-RASHID OMAR") == "AL-RASHID OMAR"


def test_name_wrapped_without_hyphen():
    lines = ["성명 Name", "NGUYEN THI THANH", "HUONG", "국가/지역", "VIETNAM"]
    assert parse_card(lines).name == "NGUYEN THI THANH HUONG"


def test_name_continuation_with_label_noise():
    # реальный вывод OCR: подпись «Name» прочитана как «Yamc», в конце шум «7»
    lines = ["성명 EGAMBERDIEV AZAM-", "Yamc AT PULATOVICH 7", "국가 지역 UZBEKISTAN"]
    assert parse_card(lines).name == "EGAMBERDIEV AZAMAT PULATOVICH"


def test_card_title_with_ocr_errors_is_not_a_name():
    # заголовок старой карты прочитан с ошибками, подпись «성명» не распознана
    lines = ["외국인등록증", "ALIEN REGISTRATON CARO", "940312-5234567", "RAKHIMOV SHERZOD", "UZBEKISTAN"]
    assert parse_card(lines).name == "RAKHIMOV SHERZOD"
    for title in ["ALLEN REGISTRATION CARD", "REGISTRATIONCARD", "RESIDENSE CARD", "ALIEN CARO"]:
        assert parse_card(["외국인등록증", title, "UZBEKISTAN"]).name == ""


def test_name_similar_to_title_word_is_kept():
    assert parse_card(["성명 Name", "ALIEV RUSTAM", "UZBEKISTAN"]).name == "ALIEV RUSTAM"
    assert parse_card(["ALIEN REGISTRATION CARD", "ALIEV RUSTAM", "UZBEKISTAN"]).name == "ALIEV RUSTAM"
    assert parse_card(["성명 Name", "CARDENAS RESIDOV", "KAZAKHSTAN"]).name == "CARDENAS RESIDOV"


def test_cis_name_endings_win_without_label():
    # подпись «성명» не распознана; лишние строки заглавными — не имя
    lines = ["외국인등록증", "HUMAN RESOURCES BRANCH", "940312-5234567",
             "ROMANOV ABDURAKHMONOVICH", "UZBEKISTAN"]
    assert parse_card(lines).name == "ROMANOV ABDURAKHMONOVICH"
    lines = ["SOME LONG NOISE TEXT HERE", "KARIMOV ALISHER BAKHTIYOR UGLI", "UZBEKISTAN"]
    assert parse_card(lines).name == "KARIMOV ALISHER BAKHTIYOR UGLI"
    lines = ["SOME LONG NOISE TEXT HERE", "RAKHIMOVA DILNOZA", "UZBEKISTAN"]
    assert parse_card(lines).name == "RAKHIMOVA DILNOZA"


def test_name_suffix_hits():
    from e9pass.ocr.front import name_suffix_hits
    assert name_suffix_hits("EGAMBERDIEV AZAMAT PULATOVICH") == 2
    assert name_suffix_hits("TURSUNOVA MADINA RUSTAMOVNA") == 3
    assert name_suffix_hits("ALIEV SHERZOD KIZI") == 2
    assert name_suffix_hits("NGUYEN VAN AN") == 0  # не СНГ: подсказки нет, работает как раньше
