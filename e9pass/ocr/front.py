"""Разбор текста, распознанного с лицевой стороны 외국인등록증 (ID-карты иностранца).

Модуль не зависит от OCR: на вход подаются строки текста, на выход — поля карты
и список предупреждений для ручной проверки.
"""

import re
from dataclasses import dataclass, field, fields
from datetime import date
from difflib import SequenceMatcher
from typing import List, Optional

# Страны (латиница на карте -> русское название). Список покрывает страны
# программы EPS (E-9) и другие частые случаи.
COUNTRIES = {
    "VIETNAM": "Вьетнам",
    "VIET NAM": "Вьетнам",
    "PHILIPPINES": "Филиппины",
    "THAILAND": "Таиланд",
    "MONGOLIA": "Монголия",
    "INDONESIA": "Индонезия",
    "SRI LANKA": "Шри-Ланка",
    "UZBEKISTAN": "Узбекистан",
    "PAKISTAN": "Пакистан",
    "CAMBODIA": "Камбоджа",
    "CHINA": "Китай",
    "BANGLADESH": "Бангладеш",
    "KYRGYZSTAN": "Киргизия",
    "KYRGYZ": "Киргизия",
    "NEPAL": "Непал",
    "MYANMAR": "Мьянма",
    "TIMOR-LESTE": "Восточный Тимор",
    "EAST TIMOR": "Восточный Тимор",
    "LAOS": "Лаос",
    "TAJIKISTAN": "Таджикистан",
    "KAZAKHSTAN": "Казахстан",
    "RUSSIA": "Россия",
    "RUSSIAN FEDERATION": "Россия",
    "INDIA": "Индия",
    "UKRAINE": "Украина",
    "TURKMENISTAN": "Туркмения",
    "AZERBAIJAN": "Азербайджан",
    "BELARUS": "Беларусь",
    "UNITED STATES": "США",
    "USA": "США",
    "JAPAN": "Япония",
}

COUNTRIES_KO = {
    "베트남": "VIETNAM",
    "필리핀": "PHILIPPINES",
    "태국": "THAILAND",
    "몽골": "MONGOLIA",
    "인도네시아": "INDONESIA",
    "스리랑카": "SRI LANKA",
    "우즈베키스탄": "UZBEKISTAN",
    "파키스탄": "PAKISTAN",
    "캄보디아": "CAMBODIA",
    "중국": "CHINA",
    "방글라데시": "BANGLADESH",
    "키르기스스탄": "KYRGYZSTAN",
    "키르기즈": "KYRGYZSTAN",
    "네팔": "NEPAL",
    "미얀마": "MYANMAR",
    "동티모르": "TIMOR-LESTE",
    "라오스": "LAOS",
    "타지키스탄": "TAJIKISTAN",
    "카자흐스탄": "KAZAKHSTAN",
    "러시아": "RUSSIA",
    "인도": "INDIA",
    "우크라이나": "UKRAINE",
}

# Слова с карты, которые не могут быть именем.
_NOT_NAME_WORDS = {
    "RESIDENCE", "CARD", "REPUBLIC", "OF", "KOREA", "KOR", "NAME", "STATUS",
    "COUNTRY", "REGION", "REGISTRATION", "NO", "ISSUE", "DATE", "SEX",
    "ALIEN", "IMMIGRATION", "OFFICE", "SERVICE", "MINISTRY", "JUSTICE",
    "PERMANENT", "RESIDENT", "NATIONALITY", "CHIEF", "AND", "FOREIGN",
}

_DIGIT_FIXES = str.maketrans({"O": "0", "o": "0", "Q": "0", "D": "0",
                              "I": "1", "l": "1", "|": "1", "!": "1",
                              "Z": "2", "S": "5", "B": "8", "G": "6"})

_DASH = r"[-–—~_=.]"


@dataclass
class CardData:
    reg_no: str = ""          # 외국인등록번호, формат 000000-0000000
    name: str = ""            # 성명 (латиница)
    country: str = ""         # 국가/지역 (латиница, как на карте)
    country_ru: str = ""      # страна по-русски
    sex: str = ""             # M / F
    birth_date: str = ""      # ГГГГ-ММ-ДД, вычисляется из номера
    visa: str = ""            # код визы, напр. E-9
    visa_full: str = ""       # как на карте, напр. 비전문취업(E-9)
    issue_date: str = ""      # 발급일자, ГГГГ-ММ-ДД
    issuer: str = ""          # орган выдачи
    warnings: List[str] = field(default_factory=list)

    def filled_count(self) -> int:
        keys = ("reg_no", "name", "country", "visa", "issue_date", "issuer")
        return sum(1 for k in keys if getattr(self, k))

    @classmethod
    def field_names(cls) -> List[str]:
        return [f.name for f in fields(cls)]


def _clean(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip()


def _fmt_date(y: int, m: int, d: int) -> Optional[str]:
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


# ---------------------------------------------------------------- номер

_CHECK_WEIGHTS = (2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4, 5)


def reg_no_checksum_ok(reg_no: str, foreign_only: bool = False) -> bool:
    """Контрольная (последняя) цифра номера. У номера иностранца (외국인등록번호) —
    (13 − Σ % 11) % 10, у корейского — (11 − Σ % 11) % 10. Подходит любая из двух."""
    d = re.sub(r"\D", "", reg_no or "")
    if len(d) != 13:
        return False
    s = sum(int(a) * w for a, w in zip(d[:12], _CHECK_WEIGHTS))
    allowed = ((13 - s % 11) % 10,) if foreign_only else ((13 - s % 11) % 10, (11 - s % 11) % 10)
    return int(d[12]) in allowed


def _repair_one_digit(head: str, tail: str) -> str:
    """Одна цифра прочитана как мусор («5760:18»): подбираем ее по контрольной цифре.
    Возвращает номер, только если подходит ровно один вариант."""
    bad = [i for i, ch in enumerate(tail) if not ch.isdigit()]
    if len(bad) != 1 or tail[0] not in "5678" and bad[0] != 0:
        return ""
    found = []
    for digit in "0123456789":
        cand = tail[:bad[0]] + digit + tail[bad[0] + 1:]
        if cand[0] in "5678" and reg_no_checksum_ok(head + cand, foreign_only=True):
            found.append(f"{head}-{cand}")
    return found[0] if len(found) == 1 else ""


def find_reg_no(lines: List[str]) -> str:
    text = "\n".join(lines)
    m = re.search(r"(?<!\d)(\d{6})\s*" + _DASH + r"?\s*([5-8]\d{6})(?!\d)", text)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    # Вторая попытка: исправляем буквы, похожие на цифры, внутри «цифровых» токенов.
    for line in lines:
        for tok in re.findall(r"[0-9OoQDIl|!ZSBG]{6}\s*" + _DASH + r"?\s*[0-9OoQDIl|!ZSBG]{7}", line):
            digits = re.sub(r"\D", "", tok.translate(_DIGIT_FIXES))
            if len(digits) == 13 and digits[6] in "5678":
                return f"{digits[:6]}-{digits[6:]}"
    # Третья попытка: одна цифра второй половины прочитана как знак — восстанавливаем по контрольной.
    for line in lines:
        for head, tail in re.findall(r"(?<!\d)(\d{6})\s*" + _DASH + r"?\s*([\d:;,'`*#.]{7})(?![\d])", line):
            fixed = _repair_one_digit(head, tail)
            if fixed:
                return fixed
    return ""


def info_from_reg_no(reg_no: str):
    """Пол и дата рождения из номера: 7-я цифра 5/6 — 1900-е, 7/8 — 2000-е; 5/7 — M, 6/8 — F."""
    digits = re.sub(r"\D", "", reg_no)
    if len(digits) != 13:
        return "", ""
    g = digits[6]
    century = {"5": 1900, "6": 1900, "7": 2000, "8": 2000}.get(g)
    if century is None:
        return "", ""
    sex = "M" if g in "57" else "F"
    birth = _fmt_date(century + int(digits[0:2]), int(digits[2:4]), int(digits[4:6]))
    return sex, birth or ""


# ---------------------------------------------------------------- виза

def find_visa(lines: List[str]):
    for line in lines:
        # "비전문취업(E-9)", "E-9-1", "E9"
        m = re.search(r"([가-힣]*)\s*\(?\s*([A-HFa-h])\s*[-–—]?\s*(\d{1,2})(?:\s*[-–—]\s*(\d{1,2}))?\s*\)", line)
        if not m:
            m = re.search(r"([가-힣]*)\s*\(?\s*\b([A-H])\s*[-–—]\s*(\d{1,2})(?:\s*[-–—]\s*(\d{1,2}))?\b", line)
        if m:
            code = f"{m.group(2).upper()}-{m.group(3)}"
            if m.group(4):
                code += f"-{m.group(4)}"
            ko = m.group(1)
            full = f"{ko}({code})" if ko else code
            return code, full
    return "", ""


# ---------------------------------------------------------------- даты

_DATE_RE = re.compile(r"((?:19|20)\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})")


def find_issue_date(lines: List[str]) -> str:
    labelled, other = [], []
    for i, line in enumerate(lines):
        for m in _DATE_RE.finditer(line):
            d = _fmt_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if not d:
                continue
            context = line + " " + (lines[i - 1] if i else "")
            if re.search(r"발급|Issue", context, re.I):
                labelled.append(d)
            else:
                other.append(d)
    today = date.today().isoformat()
    for d in labelled + other:
        if "2000" <= d <= today:
            return d
    return (labelled + other)[0] if labelled + other else ""


# ---------------------------------------------------------------- орган выдачи

def find_issuer(lines: List[str]) -> str:
    for line in reversed(lines):
        if re.search(r"출입국|외국인청|사무소", line) and "외국인등록" not in line:
            ko = "".join(re.findall(r"[가-힣·\s]+", line))
            return _clean(ko)
    return ""


# ---------------------------------------------------------------- страна

def _latin_words(line: str) -> str:
    line = re.sub(r"(?<=[A-Za-z])\s*[–—~_=]+\s*$", "-", line)  # дефис переноса, прочитанный как тире
    return _clean(re.sub(r"[^A-Za-z\- ]", " ", line)).upper()


def find_country(lines: List[str]):
    # Корейское название страны
    for line in lines:
        for ko, en in COUNTRIES_KO.items():
            # "인도" — подстрока "인도네시아", поэтому проверяем границы слова
            if re.search(r"(?<![가-힣])" + ko + r"(?![가-힣])", line):
                return en
    # Латиница: точное, затем нечеткое совпадение
    candidates = [_latin_words(l) for l in lines]
    for cand in candidates:
        for en in sorted(COUNTRIES, key=len, reverse=True):
            if re.search(r"\b" + re.escape(en) + r"\b", cand):
                return en
    best, best_ratio = "", 0.0
    for cand in candidates:
        for word in [cand] + cand.split():
            if len(word) < 5:
                continue
            for en in COUNTRIES:
                r = SequenceMatcher(None, word, en).ratio()
                if r > best_ratio:
                    best, best_ratio = en, r
    return best if best_ratio >= 0.8 else ""


# ---------------------------------------------------------------- имя

# Заголовок карты («ALIEN REGISTRATION CARD», «RESIDENCE CARD») OCR читает с ошибками —
# REGISTRATON, RESIDENSE, CARO — и точное сравнение со словарем его пропускает.
_TITLE_LONG = ("REGISTRATION", "IMMIGRATION", "RESIDENCE", "REPUBLIC", "NATIONALITY", "PERMANENT")
_TITLE_PARTS = ("REGIST", "RESIDEN", "IMMIGR", "REPUBL", "KOREA")
_TITLE_SHORT = ("ALIEN", "CARD", "KOREA")


def _similar(word: str, ref: str) -> float:
    return SequenceMatcher(None, word, ref).ratio()


def _is_card_title(words: List[str]) -> bool:
    hits = 0
    for w in words:
        if any(part in w for part in _TITLE_PARTS):
            return True
        if len(w) >= 7 and any(_similar(w, t) >= 0.8 for t in _TITLE_LONG):
            return True
        if len(w) >= 3 and any(_similar(w, t) >= 0.75 for t in _TITLE_SHORT):
            hits += 1
    # Одно похожее слово бывает в имени (ALIEV ≈ ALIEN), два — уже заголовок.
    return hits >= 2


# Типичные окончания фамилий и отчеств СНГ: ROMANOV(A), ALIEV(A), ABDURAKHMONOVICH, …OVNA,
# узбекские UGLI / KIZI (сын / дочь). Отдельные слова UGLI, OGLI, KIZI, QIZI тоже признак.
# -IN/-INA не берем: узбекские имена на -DIN (NURIDDIN, SHAMSIDDIN) — не фамилии.
_NAME_ENDINGS = ("OVICH", "EVICH", "OVNA", "EVNA", "ICH", "OVA", "EVA", "OV", "EV",
                 "UGLI", "OGLI", "KIZI", "QIZI", "SKIY", "SKAYA", "ENKO")
_NAME_PARTICLES = {"UGLI", "OGLI", "UG'LI", "O'G'LI", "KIZI", "QIZI"}


def name_suffix_hits(text: str) -> int:
    """Сколько слов похожи на фамилию/отчество СНГ — подсказка, что строка и есть ФИО."""
    hits = 0
    for w in text.split():
        if w in _NAME_PARTICLES or (len(w) >= 4 and w.endswith(_NAME_ENDINGS)):
            hits += 1
    return hits


_PATRONYMIC_ENDINGS = ("ICH", "OVNA", "EVNA")


def respace_with_hint(name: str, hint: str) -> str:
    """OCR склеил слова («IRKINOVASILBEK BAHTIYOR UGLI»), а архив назван «IRKINOV ASILBEK.zip» —
    расставляем пробелы по имени архива. Если буквы не совпадают, имя не меняется."""
    hint_words = re.findall(r"[A-Z]+", (hint or "").upper())
    if len(hint_words) < 2:
        return name
    joined = "".join(hint_words)
    letters = name.replace(" ", "")
    if not letters.startswith(joined) or " ".join(hint_words) in name:
        return name
    # сколько слов OCR-имени покрывает склейка из архива
    consumed, words = "", name.split()
    for i, w in enumerate(words):
        consumed += w
        if consumed == joined:
            return " ".join(hint_words + words[i + 1:])
        if len(consumed) > len(joined):
            break
    return name


def _drop_label_noise(name: str) -> str:
    """Обрывок подписи «성명 Name» перед фамилией: «MY ABILOV BOBORAKHIM» -> «ABILOV BOBORAKHIM».

    Убирается только 1–2 буквы перед фамилией на -OV/-EV(A): вьетнамское «LE VAN AN» остается.
    """
    words = name.split()
    if len(words) >= 3 and len(words[0]) <= 2 and words[1].endswith(("OV", "EV", "OVA", "EVA")):
        return " ".join(words[1:])
    return name


def order_fio(name: str) -> str:
    """Отчество всегда в конце: Фамилия Имя Отчество.

    Если строки карты прочитаны не по порядку, OCR дает «TOJIEVICH SAYITKHUJAEV UMID» —
    отчество (…ICH, …OVNA/EVNA или «имя отца + UGLI/KIZI») переносится в конец.
    """
    words = name.split()
    if len(words) < 3:  # без имени отчества не бывает: «KARPOVICH IVAN» — фамилия на -ICH
        return name
    groups, i = [], 0
    while i < len(words):
        if i + 1 < len(words) and words[i + 1] in _NAME_PARTICLES:
            groups.append((words[i:i + 2], True))
            i += 2
        else:
            groups.append(([words[i]], words[i].endswith(_PATRONYMIC_ENDINGS) and len(words[i]) > 5))
            i += 1
    if len(groups) < 3 or groups[-1][1]:
        return name
    patronymic = [g for g, is_p in groups if is_p]
    if len(patronymic) != 1:
        return name
    rest = [w for g, is_p in groups if not is_p for w in g]
    return " ".join(rest + patronymic[0])


def _is_name_like(text: str) -> bool:
    words = text.split()
    if not words or len(text) < 3:
        return False
    if not re.fullmatch(r"[A-Z][A-Z\-' ]+", text):
        return False
    if any(w in _NOT_NAME_WORDS for w in words) or _is_card_title(words):
        return False
    if text in COUNTRIES:
        return False
    return True


def normalize_name(name: str) -> str:
    """Склеивает перенос строки с дефисом: «AZAM- AT PULATOVICH» -> «AZAMAT PULATOVICH».

    Настоящий дефис в имени (AL-RASHID) пишется без пробела и не трогается.
    """
    name = re.sub(r"([A-Z])\s*[-–—~_=]\s+([A-Z])", r"\1\2", name.strip())
    name = re.sub(r"[-–—~_=]+$", "", name).strip()
    # Дефис переноса OCR иногда теряет: «JOHONGIR B AKHRITDIN». Одиночная буква внутри имени
    # не бывает отдельным словом — приклеиваем ее к следующему: «BAKHRITDIN».
    name = re.sub(r"(?<=[A-Z] )([A-Z])\s+(?=[A-Z]{2})", r"\1", name)
    # Потерянный пробел после фамилии: «KAKHRAMONOVIBRA» -> «KAKHRAMONOV IBRA». После «-OV/-EV» на «A»
    # не делим: это может быть женская фамилия (TURSUNOVA).
    name = re.sub(r"\b([A-Z]{4,}(?:OV|EV))(?=[B-DF-HJ-NP-TV-Z]?[A-Z]{3,}\b)(?!A|ICH|NA|NA\b)(?=[A-Z])",
                  lambda m: m.group(1) + " ", name)
    name = re.sub(r"\bUGL\b", "UGLI", name)
    name = re.sub(r"\bOGL\b", "OGLI", name)
    return re.sub(r"\s+", " ", name)


def _looks_like_country(word: str) -> bool:
    return any(SequenceMatcher(None, word, c).ratio() >= 0.8 for c in COUNTRIES)


def _name_line(line: str, country: str) -> str:
    """Имя латиницей из строки-продолжения: только слова ЗАГЛАВНЫМИ буквами.

    Подпись «Name» под корейским «성명» OCR часто читает как мусор («Yamc AT PULATOVICH 7») —
    слова со строчными буквами и цифры отбрасываются. Строки с корейским текстом — не имя.
    """
    line = _strip_korean_prefix(line)
    if re.search(r"[가-힣]", line):
        return ""
    line = re.sub(r"(?<=[A-Za-z])\s*[-–—~_=]+\s*$", "-", line)
    words = re.findall(r"(?<![A-Za-z])[A-Z][A-Z'\-]*(?![A-Za-z])", line)
    cand = " ".join(words)
    if len(cand.replace(" ", "")) < 2 or not _is_name_like(cand):
        return ""
    if cand == country or _looks_like_country(cand):
        return ""
    return cand


def _strip_korean_prefix(line: str) -> str:
    """Убирает обрывки корейских подписей в начале строки: «틀 AKHTIYOR UGLI», «명 KARIMOV»."""
    words = _clean(line).split(" ")
    for _ in range(2):
        if words and re.fullmatch(r"[가-힣/:.]{1,4}", words[0]):
            words = words[1:]
    return " ".join(words)


def _with_continuation(first: str, rest_lines: List[str], country: str) -> str:
    """Длинное имя переносится на следующую строку карты — присоединяем ее."""
    name = first
    for nxt in rest_lines[:2]:
        cont = _name_line(nxt, country)
        if not cont:
            break
        if name.endswith("-") or _cannot_start_word(cont):
            # «IBRA-» + «TBEK» — перенос; дефис OCR мог потерять, но слово не начинается с «TB»
            name = f"{name.rstrip('-').rstrip()}{cont}"
        else:
            name = f"{name} {cont}"
    return normalize_name(name)


_VOWELS = set("AEIOUY")
_WORD_STARTS = {"SH", "CH", "KH", "ZH", "TS", "GH", "PH", "TH", "ST", "SK", "SP", "SV", "SN", "SM", "SL",
                "BR", "KR", "PR", "TR", "DR", "GR", "FR", "VR", "BL", "KL", "PL", "FL", "GL", "YU", "YA", "YO"}


def _cannot_start_word(text: str) -> bool:
    """Две согласные, с которых имя не начинается («TBEK», «RBEK») — это вторая половина переноса."""
    w = text.split()[0] if text.split() else ""
    return (len(w) >= 3 and w[0] not in _VOWELS and w[1] not in _VOWELS
            and w[:2] not in _WORD_STARTS)


def find_name(lines: List[str], country: str = "") -> str:
    # 1) Подпись "성명" / "Name": имя в той же строке после неё или в следующей.
    for i, line in enumerate(lines):
        # «성 명» OCR иногда читает как одно «명» перед именем.
        m = re.search(r"(성\s*명|^\s*명(?=\s+[A-Z])|Name)\s*[:.]?\s*(.*)", line, re.I)
        if not m:
            continue
        # OCR часто искажает саму подпись «Name» (Namne, Narne) — убираем похожие слова.
        rest = " ".join(w for w in _latin_words(m.group(2)).split()
                        if SequenceMatcher(None, w, "NAME").ratio() < 0.6)
        if _is_name_like(rest) and rest != country:
            return _with_continuation(rest, lines[i + 1:], country)
        for j in range(i + 1, min(i + 3, len(lines))):
            cand = _name_line(lines[j], country)
            if cand:
                return _with_continuation(cand, lines[j + 1:], country)
    # 2) Строка, похожая на имя: сначала с окончаниями фамилии/отчества (-OV, -ICH, UGLI…),
    #    при равенстве — самая длинная.
    #    Перенос имени на следующую строку присоединяется.
    best, best_key = "", (0, 0)
    for i, line in enumerate(lines):
        raw = _strip_korean_prefix(line)
        if re.search(r"\d", raw):
            continue
        # подпись «Name» мелкими буквами рядом с именем не мешает: берем только слова ЗАГЛАВНЫМИ
        cand = _name_line(raw, country) if raw != raw.upper() else _latin_words(raw)
        if not cand or not _is_name_like(cand) or cand == country:
            continue
        if cand.endswith("-") or name_suffix_hits(cand):
            cand = _with_continuation(cand, lines[i + 1:], country)
        key = (name_suffix_hits(cand), len(cand))
        if key > best_key:
            best, best_key = cand, key
    return normalize_name(best)


# ---------------------------------------------------------------- пол

def find_sex(lines: List[str]) -> str:
    for line in lines:
        m = re.search(r"(성\s*별|Sex)\s*[:.]?\s*(?:Sex)?\s*([MF남여])\b", line, re.I)
        if m:
            v = m.group(2).upper()
            return {"남": "M", "여": "F"}.get(v, v)
    return ""


# ---------------------------------------------------------------- всё вместе

_TOP = re.compile(r"외국인등록증|RESIDENCE|등록번호|Registration", re.I)
_BOTTOM = re.compile(r"Issue|발급|CHIEF|출입국|외국인청|사무소|OFFICE", re.I)


def front_orientation_ok(lines: List[str]) -> int:
    """1 — строки идут сверху вниз: заголовок карты выше подписи «발급일자 / CHIEF … OFFICE»."""
    top = next((i for i, line in enumerate(lines) if _TOP.search(line)), None)
    bottom = next((i for i in range(len(lines) - 1, -1, -1) if _BOTTOM.search(lines[i])), None)
    return int(top is not None and bottom is not None and top < bottom)


def parse_card(lines: List[str]) -> CardData:
    lines = [_clean(l) for l in lines if l and l.strip()]
    data = CardData()
    data.reg_no = find_reg_no(lines)
    data.visa, data.visa_full = find_visa(lines)
    data.issue_date = find_issue_date(lines)
    data.issuer = find_issuer(lines)
    data.country = find_country(lines)
    data.country_ru = COUNTRIES.get(data.country, "")
    data.name = order_fio(_drop_label_noise(find_name(lines, data.country)))

    sex_from_no, data.birth_date = info_from_reg_no(data.reg_no)
    sex_printed = find_sex(lines)
    data.sex = sex_printed or sex_from_no

    if not data.reg_no:
        data.warnings.append("номер не найден")
    elif not data.birth_date:
        data.warnings.append("номер: неверная дата рождения")
    elif not reg_no_checksum_ok(data.reg_no):
        data.warnings.append("номер: контрольная цифра не сходится — сверьте с картой")
    if sex_printed and sex_from_no and sex_printed != sex_from_no:
        data.warnings.append("пол на карте не совпадает с номером")
    for key, label in (("name", "ФИО"), ("country", "страна"),
                       ("visa", "виза"), ("issue_date", "дата выдачи")):
        if not getattr(data, key):
            data.warnings.append(f"{label} не найдено")
    return data
