"""Банковская книжка (통장사본): банк и номер счета."""

import re
from typing import List, Optional, Tuple

# ключевое слово на книжке -> название банка
BANKS = [
    ("카카오뱅크", "카카오뱅크"), ("kakaobank", "카카오뱅크"), ("토스뱅크", "토스뱅크"),
    ("케이뱅크", "케이뱅크"), ("국민은행", "KB국민은행"), ("KB", "KB국민은행"), ("국민", "KB국민은행"),
    ("신한", "신한은행"), ("SHINHAN", "신한은행"), ("우리은행", "우리은행"), ("WOORI", "우리은행"),
    ("하나은행", "하나은행"), ("KEB", "하나은행"), ("HANA", "하나은행"), ("농협", "NH농협은행"),
    ("NH", "NH농협은행"), ("기업은행", "IBK기업은행"), ("IBK", "IBK기업은행"), ("우체국", "우체국"),
    ("새마을", "새마을금고"), ("MG", "새마을금고"), ("신협", "신협"), ("수협", "수협은행"),
    ("SC제일", "SC제일은행"), ("제일은행", "SC제일은행"), ("씨티", "한국씨티은행"), ("CITI", "한국씨티은행"),
    ("부산은행", "부산은행"), ("BNK", "부산은행"), ("대구은행", "iM뱅크"), ("iM뱅크", "iM뱅크"),
    ("경남은행", "경남은행"), ("광주은행", "광주은행"), ("전북은행", "전북은행"), ("제주은행", "제주은행"),
    ("우리", "우리은행"), ("하나", "하나은행"),
]

_ACC = re.compile(r"(?<![\d-])(\d{2,6}(?:[-\s]\d{2,8}){1,3})(?![\d-])")
_ACC_PLAIN = re.compile(r"(?<!\d)(\d{10,14})(?!\d)")
_DATE = re.compile(r"(?:19|20)\d{2}[.\-/]\d{1,2}[.\-/]\d{1,2}")


def find_bank(lines: List[str]) -> str:
    text = " ".join(lines)
    upper = text.upper()
    for kw, name in BANKS:
        # латинские сокращения (NH, KB) часто слиты с корейским текстом: «NH농협은행»
        if (kw in text) if not kw.isascii() else re.search(r"(?<![A-Z])" + kw + r"(?![A-Z])", upper):
            return name
    m = re.search(r"[가-힣]{2,6}은행", text)  # неизвестный банк или ошибка OCR в названии
    return m.group(0) if m else ""


def _valid_account(raw: str) -> bool:
    digits = re.sub(r"\D", "", raw)
    if not 10 <= len(digits) <= 16:
        return False
    if digits.startswith("010") and len(digits) == 11:  # номер телефона
        return False
    if re.fullmatch(r"\d{6}[5-8]\d{6}", digits):  # номер ARC
        return False
    return True


def find_account(lines: List[str]) -> str:
    labelled, other = [], []
    for i, line in enumerate(lines):
        clean = _DATE.sub(" ", line)
        context = line + " " + (lines[i - 1] if i else "")
        is_label = re.search(r"계좌|Account|A/C|No\.", context, re.I)
        for m in list(_ACC.finditer(clean)) + list(_ACC_PLAIN.finditer(clean)):
            raw = re.sub(r"\s+", "-", m.group(1).strip())
            if _valid_account(raw):
                (labelled if is_label else other).append(raw)
    for acc in labelled + other:
        return acc
    return ""


def parse_bank(lines: List[str]) -> Tuple[str, str, List[str]]:
    """(банк, счет, предупреждения)."""
    bank, acc = find_bank(lines), find_account(lines)
    warnings = []
    if not bank:
        warnings.append("банк не распознан")
    if not acc:
        warnings.append("номер счета не распознан")
    return bank, acc, warnings


def bank_score(lines: List[str]) -> Tuple[int, Optional[str]]:
    text = " ".join(lines)
    score, reason = 0, []
    bank = find_bank(lines)
    if bank:
        score += 3
        reason.append(bank)
    for kw in ("계좌", "통장", "예금", "Account", "거래"):
        if kw.lower() in text.lower():
            score += 2 if kw in ("계좌", "통장") else 1
            reason.append(kw)
    return score, ", ".join(reason) or None
