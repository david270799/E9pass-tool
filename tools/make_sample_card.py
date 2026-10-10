"""Генерирует ВЫМЫШЛЕННУЮ ID-карту иностранца для проверки программы.

    python tools/make_sample_card.py шрифт.ttf out_dir

Нужен шрифт с корейскими символами (например, Noto Sans KR или Malgun Gothic:
C:\\Windows\\Fonts\\malgun.ttf). Создаются ровный «скан» и «фото с телефона»
(карта на фоне, под наклоном).
"""

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

SAMPLES = [
    dict(reg_no="950101-5123456", name="NGUYEN VAN AN", country="VIETNAM",
         sex="M", visa="비전문취업(E-9)", issue="2023.05.12", issuer="수원출입국·외국인사무소장"),
    dict(reg_no="000315-8234567", name="TURSUNOVA MADINA", country="UZBEKISTAN",
         sex="F", visa="비전문취업(E-9)", issue="2024.11.03", issuer="서울출입국·외국인청장"),
]


def draw_card(s, font_path) -> Image.Image:
    W, H = 1012, 638  # 85.6 x 54 мм
    card = Image.new("RGB", (W, H), (228, 238, 246))
    d = ImageDraw.Draw(card)
    f = lambda size, wght=400: ImageFont.truetype(font_path, size)
    d.text((40, 30), "외국인등록증", font=f(44), fill=(20, 40, 90))
    d.text((640, 42), "RESIDENCE CARD", font=f(34), fill=(20, 40, 90))
    d.rectangle((40, 130, 300, 470), fill=(180, 190, 200))  # место для фото
    small, big = f(22), f(32)
    rows = [
        ("외국인등록번호 Registration No.", s["reg_no"]),
        ("성명 Name", s["name"]),
        ("국가/지역 Country/Region", s["country"]),
        ("성별 Sex", s["sex"]),
        ("체류자격 Status", s["visa"]),
        ("발급일자 Issue Date", s["issue"]),
    ]
    y = 125
    for label, value in rows:
        d.text((330, y), label, font=small, fill=(70, 80, 100))
        d.text((330, y + 26), value, font=big, fill=(0, 0, 0))
        y += 76
    d.text((560, 580), s["issuer"], font=f(30), fill=(0, 0, 0))
    return card


def as_phone_photo(card: Image.Image) -> Image.Image:
    bg = Image.new("RGB", (1600, 1200), (120, 95, 70))
    rotated = card.rotate(7, expand=True, fillcolor=(120, 95, 70))
    bg.paste(rotated, (230, 220))
    return bg.filter(ImageFilter.GaussianBlur(0.8))


def main():
    font_path, out = sys.argv[1], Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    for i, s in enumerate(SAMPLES, start=1):
        card = draw_card(s, font_path)
        card.save(out / f"sample{i}_scan.png")
        as_phone_photo(card).save(out / f"sample{i}_photo.jpg", quality=85)
        # Фото, снятое «боком» (телефон повернут)
        as_phone_photo(card).rotate(90, expand=True).save(out / f"sample{i}_sideways.jpg", quality=85)
    print(f"Готово: {out}")


if __name__ == "__main__":
    main()
