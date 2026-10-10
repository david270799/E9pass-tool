"""Генерирует ВЫМЫШЛЕННЫЕ документы клиентов для проверки программы.

    python tools/make_samples.py шрифт.ttf out_dir

Нужен шрифт с корейскими символами (Noto Sans KR или C:\\Windows\\Fonts\\malgun.ttf).
Создается:
  archives/*.zip       — архивы клиентов: лицо ID, оборот ID, 통장 (фото «с телефона»)
  later/<клиент>/*.pdf — документы для догрузки: договор (с паролем = дата рождения),
                         страховка, справка с неизвестным паролем
"""

import io
import sys
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

CLIENTS = [
    dict(reg_no="950101-5123456", name="NGUYEN VAN AN", country="VIETNAM", sex="M",
         visa="비전문취업(E-9)", issue="2023.05.12", issuer="수원출입국·외국인사무소장",
         addresses=[("2023.05.12", "경기도 수원시 팔달구 효원로 123", "팔달구청장"),
                    ("2024.02.01", "경기도 화성시 향남읍 발안로 45, 101동 202호", "화성시장")],
         bank="KB국민은행", account="123456-01-234567"),
    dict(reg_no="000315-8234567", name="TURSUNOVA MADINA", country="UZBEKISTAN", sex="F",
         visa="비전문취업(E-9)", issue="2024.11.03", issuer="서울출입국·외국인청장",
         addresses=[("2024.11.03", "경상남도 김해시 주촌면 서부로 1500", "김해시장")],
         bank="NH농협은행", account="302-1234-5678-91"),
    dict(reg_no="910722-5345678", name="BATBAYAR GANBOLD", country="MONGOLIA", sex="M",
         visa="비전문취업(E-9)", issue="2022.08.20", issuer="인천출입국·외국인청장",
         addresses=[("2022.08.20", "인천광역시 남동구 남동대로 215", "남동구청장")],
         bank="신한은행", account="110-123-456789"),
]


def font(path, size):
    return ImageFont.truetype(path, size)


def card_front(s, fp) -> Image.Image:
    W, H = 1012, 638
    card = Image.new("RGB", (W, H), (228, 238, 246))
    d = ImageDraw.Draw(card)
    d.text((40, 30), "외국인등록증", font=font(fp, 44), fill=(20, 40, 90))
    d.text((640, 42), "RESIDENCE CARD", font=font(fp, 34), fill=(20, 40, 90))
    d.rectangle((40, 130, 300, 470), fill=(180, 190, 200))
    rows = [("외국인등록번호 Registration No.", s["reg_no"]), ("성명 Name", s["name"]),
            ("국가/지역 Country/Region", s["country"]), ("성별 Sex", s["sex"]),
            ("체류자격 Status", s["visa"]), ("발급일자 Issue Date", s["issue"])]
    y = 125
    for label, value in rows:
        d.text((330, y), label, font=font(fp, 22), fill=(70, 80, 100))
        d.text((330, y + 26), value, font=font(fp, 32), fill=(0, 0, 0))
        y += 76
    d.text((560, 580), s["issuer"], font=font(fp, 30), fill=(0, 0, 0))
    return card


def card_back(s, fp) -> Image.Image:
    W, H = 1012, 638
    card = Image.new("RGB", (W, H), (236, 240, 244))
    d = ImageDraw.Draw(card)
    d.text((30, 20), "체류기간 연장허가  Extension of Period of Sojourn", font=font(fp, 24), fill=(20, 40, 90))
    d.text((30, 60), "허가일자     만료일자     확인", font=font(fp, 22), fill=(70, 80, 100))
    d.text((30, 95), "2024.05.12   2027.05.11   수원출입국", font=font(fp, 24), fill=(0, 0, 0))
    d.line((20, 150, W - 20, 150), fill=(150, 160, 170), width=2)
    d.text((30, 165), "체류지 변경신고  Report of Change of Address", font=font(fp, 24), fill=(20, 40, 90))
    d.text((30, 205), "신고일          체류지                                   확인", font=font(fp, 22),
           fill=(70, 80, 100))
    y = 245
    for date, addr, stamp in s["addresses"]:
        d.text((30, y), date, font=font(fp, 24), fill=(0, 0, 0))
        d.text((200, y), addr, font=font(fp, 24), fill=(0, 0, 0))
        d.text((820, y), stamp, font=font(fp, 20), fill=(160, 40, 40))
        y += 60
    return card


def bankbook(s, fp) -> Image.Image:
    W, H = 900, 1250
    page = Image.new("RGB", (W, H), (250, 248, 240))
    d = ImageDraw.Draw(page)
    d.rectangle((0, 0, W, 140), fill=(255, 204, 0))
    d.text((40, 40), s["bank"], font=font(fp, 56), fill=(60, 50, 40))
    d.text((40, 200), "보통예금 통장", font=font(fp, 40), fill=(0, 0, 0))
    d.text((40, 300), "계좌번호", font=font(fp, 30), fill=(90, 90, 90))
    d.text((40, 345), s["account"], font=font(fp, 44), fill=(0, 0, 0))
    d.text((40, 450), "예금주", font=font(fp, 30), fill=(90, 90, 90))
    d.text((40, 495), s["name"], font=font(fp, 44), fill=(0, 0, 0))
    d.text((40, 600), "신규일 2023.05.20", font=font(fp, 30), fill=(90, 90, 90))
    return page


def as_photo(img: Image.Image, angle=6, bg=(120, 95, 70)) -> Image.Image:
    canvas = Image.new("RGB", (int(img.width * 1.6), int(img.height * 1.6)), bg)
    rot = img.rotate(angle, expand=True, fillcolor=bg)
    canvas.paste(rot, ((canvas.width - rot.width) // 2, (canvas.height - rot.height) // 2))
    return canvas.filter(ImageFilter.GaussianBlur(0.7))


def jpg_bytes(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def make_pdf(fp, title, lines, out: Path, password: str = ""):
    import pikepdf
    import pymupdf

    doc = pymupdf.open()
    for page_no in range(2):
        page = doc.new_page()
        page.insert_font(fontname="kr", fontfile=fp)
        page.insert_text((60, 80), title if page_no == 0 else f"{title} (2)", fontname="kr", fontsize=22)
        y = 130
        for line in lines:
            page.insert_text((60, y), line, fontname="kr", fontsize=12)
            y += 22
    doc.save(out)
    doc.close()
    if password:
        with pikepdf.open(out, allow_overwriting_input=True) as pdf:
            pdf.save(out, encryption=pikepdf.Encryption(user=password, owner=password + "x"))


def main():
    fp, out = sys.argv[1], Path(sys.argv[2])
    (out / "archives").mkdir(parents=True, exist_ok=True)
    for i, s in enumerate(CLIENTS, start=1):
        front = as_photo(card_front(s, fp), 6)
        back = as_photo(card_back(s, fp), -4)
        book = as_photo(bankbook(s, fp), 3, bg=(90, 90, 100))
        zpath = out / "archives" / f"{s['name'].replace(' ', '_')}.zip"
        with zipfile.ZipFile(zpath, "w") as zf:
            # Имена как у фото с телефона/KakaoTalk — по ним тип не угадать
            zf.writestr(f"IMG_40{i}1.jpg", jpg_bytes(front))
            zf.writestr(f"IMG_40{i}2.jpg", jpg_bytes(back))
            zf.writestr(f"KakaoTalk_2026080{i}.jpg", jpg_bytes(book))
        later = out / "later" / s["name"].replace(" ", "_")
        later.mkdir(parents=True, exist_ok=True)
        yyyymmdd = ("19" if s["reg_no"][7] in "56" else "20") + s["reg_no"][:6]
        make_pdf(fp, "표준근로계약서", [f"근로자: {s['name']}", "계약기간: 2026.01.01 ~ 2028.12.31",
                                    "근무장소: 경기도 화성시", "(вымышленный документ для теста)"],
                 later / f"근로계약서_{s['name'].split()[0]}.pdf", password=yyyymmdd)
        make_pdf(fp, "보험 가입증명서", [f"피보험자: {s['name']}", "출국만기보험 / 보증보험"],
                 later / "document_scan_01.pdf", password=s["reg_no"][:6] if i == 2 else "")
        if i == 1:
            make_pdf(fp, "소득금액증명", ["2025년 귀속"], later / "income_2025.pdf", password="0000")
    print(f"Готово: {out}")


if __name__ == "__main__":
    main()
