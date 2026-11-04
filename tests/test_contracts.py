"""Договоры E9PASS (서비스 신청서) → ФИО, дата рождения, телефон, e-mail → Excel."""

import pymupdf
from openpyxl import load_workbook

from e9pass import contracts, services

FORM = """E9PASS 서비스 신청서(개인범용)
공동인증서를 신청하는 본인의 정보
신청번호
261021-1076-0000
이름
{name}
주민등록번호
{birth}*******
전화
02-1899-6943
휴대폰 번호
{phone}
이메일
{email}
주소
경기도 수원시 예시로 1
2026 년   10 월   21 일
이름 :  {sign1}
{sign2}
(인)
방문일
성명
자필서명"""


def make_contract(path, name="karimov jasur bakhtiyor ugli", birth="900101", phone="010-1234-5678",
                  email="jasur.karimov@gmail.com"):
    words = name.split()
    text = FORM.format(name=name, birth=birth, phone=phone, email=email, sign1=" ".join(words[:2]),
                       sign2=" ".join(words[2:]))
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 60), text, fontname="korea", fontsize=10)
    doc.save(path)
    doc.close()
    return path


def test_text_pdf_fields(tmp_path):
    r = contracts.read_contract(make_contract(tmp_path / "a.pdf"))
    assert r.source == "текст PDF"
    assert (r.full_name, r.birth_date, r.birth_dotted) == ("KARIMOV JASUR BAKHTIYOR UGLI", "900101", "01.01.1990")
    assert r.phone == "010-1234-5678"        # мобильный, а не «전화 02-1899-6943»
    assert r.email == "jasur.karimov@gmail.com" and not r.problems


def test_merged_ocr_lines():
    # на скане ячейки таблицы сливаются в одну строку, фамилия в подписи уходит на следующую строку
    text = ("신청번호 261021-1076-0000\n이름 boboev rustam ravshanovich 주민등록번호 950303*******\n"
            "후 02-1899-6943 휴대폰 번호 010 3456 7890\nrustam95@naver.com\n이름:boboev rustam (인)\nravshanovich")
    f = contracts.parse_contract_text(text)
    assert f == {"full_name": "BOBOEV RUSTAM RAVSHANOVICH", "birth_date": "950303", "phone": "010-3456-7890",
                 "email": "rustam95@naver.com"}


def test_missing_fields_reported():
    f = contracts.parse_contract_text("이름\nnguyen van an\n전화\n02-1899-6943")
    r = contracts.ContractData(file="x.pdf", **f)
    assert r.full_name == "NGUYEN VAN AN" and r.problems == ["нет даты рождения", "нет телефона"]


def test_excel_export_importable_as_registry(tmp_path):
    rows = [contracts.read_contract(make_contract(tmp_path / "a.pdf")),
            contracts.read_contract(make_contract(tmp_path / "b.pdf", name="saidova madina akmalovna",
                                                  birth="970707", phone="010-2345-6789", email="m@naver.com"))]
    out = contracts.export_contracts(rows, tmp_path / "c.xlsx")
    ws = load_workbook(out).active
    header = [c.value for c in ws[1]]
    assert header[:6] == ["№", "ФИО", "Дата рождения", "Дата рождения (ДД.ММ.ГГГГ)", "Телефон", "E-mail"]
    assert [c.value for c in ws[3]][1:6] == ["SAIDOVA MADINA AKMALOVNA", "970707", "07.07.1997", "010-2345-6789",
                                             "m@naver.com"]
    # те же заголовки, что у реестра: «Импорт реестра» найдет ФИО, телефон и e-mail
    assert {"name", "phone", "email"} <= set(services._header_map([str(h) for h in header]))


def test_manual_input_normalized():
    assert contracts.normalize_mobile("010 9999 8888") == "010-9999-8888"
    assert contracts.normalize_mobile("01099998888") == "010-9999-8888"
    assert [contracts.normalize_birth(x) for x in ("930316", "93-03-16", "16.03.1993", "1993.03.16", "991399")] == \
        ["930316", "930316", "930316", "930316", ""]
