"""Оборотная сторона 외국인등록증: адрес регистрации (체류지).

На обороте таблица «신고일 | 체류지 | 확인». При переезде дописывают строку, поэтому
берем последний распознанный адрес; если он не читается — любой другой распознанный.
"""

import re
from typing import List, Optional, Tuple

_DATE = re.compile(r"^\s*((?:19|20)\d{2})\s*[.\-/년\s]\s*(\d{1,2})\s*[.\-/월\s]\s*(\d{1,2})\s*[.일]?\s*")
_REGION = ("서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종", "경기", "강원", "충청",
           "충북", "충남", "전라", "전북", "전남", "경상", "경북", "경남", "제주")
_UNIT_BIG = re.compile(r"[가-힣]+(?:특별시|광역시|특별자치시|특별자치도|도|시|군|구)(?![가-힣])")
_UNIT_SMALL = re.compile(r"[가-힣0-9]+(?:읍|면|동|리|로|길|가)(?![가-힣])")
_HEADER = re.compile(r"신고일|체류지|확인|Date|Address|Confirm|Report", re.I)
# Хвост строки — штамп/подпись в колонке 확인 ("팔달구청장", "수원출입국외국인사무소장").
_STAMP = re.compile(r"\s+[가-힣·]*(?:청장|소장|사무소|출입국|외국인청|주민센터)[가-힣·]*\s*$")


def is_address(text: str) -> bool:
    has_region = any(r in text for r in _REGION)
    big = bool(_UNIT_BIG.search(text))
    small = bool(_UNIT_SMALL.search(text))
    return (has_region and (big or small)) or (big and small)


def clean_address(text: str) -> str:
    text = _DATE.sub("", text)
    text = _HEADER.sub(" ", text)
    for _ in range(2):
        text = _STAMP.sub("", text)
        # штамп из колонки 확인 с ошибкой OCR: «남동구정장», «화성시장»
        text = re.sub(r"\s+[가-힣·]{2,}장\s*$", "", text)
    text = re.sub(r"[^가-힣0-9A-Za-z\-,()·.\s]", " ", text)
    # Имя латиницей с края карты (IRKINOV ASILBEK BAHT…) — не часть адреса.
    text = re.sub(r"(?:\b[A-Z]{3,}\b\s*){2,}", " ", text)
    # Мусор перед адресом (штамп соседней строки) — адрес начинается с названия региона.
    starts = [m.start() for r in _REGION for m in re.finditer(r"(?<![가-힣])" + r, text)]
    if starts and min(starts) > 0:
        text = text[min(starts):]
    text = re.sub(r"\s+", " ", text).strip(" ,.-")
    text = re.sub(r"(?<=\d) [가-힣]$", "", text)  # обрывок штампа после номера дома: «서부로 1500 워»
    return format_address(text)


_BIG = r"(?:특별자치도|특별자치시|특별시|광역시|도|시|군|구|읍|면)"


def format_address(text: str) -> str:
    """Пробелы как на карте: «경상남도밀양시하남읍» -> «경상남도 밀양시 하남읍»,
    «16-8,101동1514호(OO타운)» -> «16-8, 101동 1514호 (OO타운)», «원제2길16-8» -> «원제2길 16-8»."""
    def split_units(word: str) -> str:
        # OCR склеил слова: пробел после 도/시/군/구/읍/면, если дальше снова корейское слово.
        # Не трогаем «대구광역시» (дальше 광역시) и «진도군» (дальше одна буква-единица).
        return re.sub(r"([가-힣]+?" + _BIG + r")(?=[가-힣])(?!광역시|특별시|특별자치)(?![가-힣](?![가-힣0-9]))",
                      r"\1 ", word)

    text = re.sub(r"[가-힣]{4,}", lambda m: split_units(m.group()), text)
    text = re.sub(r"(\d)\.\s*(\d+\s*(?:호|층))", r"\1, \2", text)   # «9-1.504호» -> «9-1, 504호»
    text = re.sub(r"(길)(?=\d)", r"\1 ", text)                 # «길9-1» -> «길 9-1»
    text = re.sub(r"(로)(?=\d+(?:[-,\s]|$))", r"\1 ", text)   # «효원로123» (но не «코모로341번길»)
    text = re.sub(r"\s*,\s*", ", ", text)
    text = re.sub(r"(\d)(동)(?=\d)", r"\1\2 ", text)            # «101동1514호» -> «101동 1514호»
    text = re.sub(r"(?<=\S)\(", " (", text)
    return re.sub(r"\s+", " ", text).strip(" ,")


def _join_row(parts: List[str]) -> str:
    """Перенос адреса на следующую строку таблицы: «시동3리2» + «길 9-1» -> «시동3리2길 9-1»."""
    out = ""
    for part in parts:
        # сначала убрать подписи колонок и имя с края карты, иначе перенос не склеится
        part = re.sub(r"(?:\b[A-Z]{3,}\b\s*){2,}", " ", _HEADER.sub(" ", part)).strip()
        if out and re.match(r"(?:번길|길|로|동|리|호|번지)(?![가-힣])", part) and re.search(r"[0-9가-힣]$", out):
            out += part
        else:
            out = f"{out} {part}" if out else part
    return out


def _is_continuation(line: str) -> bool:
    if re.search(r"체류기간|허가|만료|Period|Permit", line, re.I):
        return False
    part = clean_address(line)
    return bool(part) and bool(_UNIT_SMALL.search(part) or _UNIT_BIG.search(part)
                               or re.search(r"\d+\s*(?:동|호|층|번지)", part))


def parse_addresses(lines: List[str]) -> List[str]:
    """Все адреса с оборота сверху вниз."""
    rows: List[List[str]] = []
    # OCR может склеить штамп прошлой строки с датой следующей: «팔달구청장 2024.02.01 경기도…»
    split_lines = []
    for line in lines:
        split_lines += [p for p in re.split(r"\s+(?=(?:19|20)\d{2}\s*[.\-/\s]\s*\d{1,2}\s*[.\-/\s]\s*\d{1,2})", line)
                        if p.strip()]
    for line in split_lines:
        if _DATE.match(line):
            rows.append([line])
        elif rows and _is_continuation(line):
            rows[-1].append(line)  # перенос длинного адреса на следующую строку
    found = []
    for row in rows:
        addr = clean_address(_join_row(row))
        if is_address(addr):
            found.append(addr)
    if not found:  # таблица не распозналась — берем строки, похожие на адрес
        for line in lines:
            addr = clean_address(line)
            if is_address(addr) and len(addr) >= 8:
                found.append(addr)
    return found


def parse_back(lines: List[str]) -> Tuple[str, List[str]]:
    """(адрес, предупреждения)."""
    addrs = parse_addresses(lines)
    if not addrs:
        return "", ["адрес на обороте не найден"]
    return addrs[-1], []


def back_score(lines: List[str]) -> Tuple[int, Optional[str]]:
    text = " ".join(lines)
    score = 0
    reason = []
    for kw in ("체류지", "신고일", "Address"):
        if kw in text:
            score += 3
            reason.append(kw)
    if re.search(r"체류기간|허가", text):
        score += 1
    if parse_addresses(lines):
        score += 2
        reason.append("адрес")
    return score, ", ".join(reason) or None
