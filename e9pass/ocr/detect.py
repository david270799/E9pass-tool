"""Определение, что на фото из архива: лицо ID, оборот ID или банковская книжка."""

import re
from typing import List, Tuple

from .back import back_score
from .bank import bank_score
from .front import find_reg_no

FRONT, BACK, BANK, UNKNOWN = "id_front", "id_back", "bankbook", ""


def front_score(lines: List[str]) -> Tuple[int, str]:
    text = " ".join(lines)
    score, reason = 0, []
    if re.search(r"외국인\s*등록증|RESIDENCE\s*CARD|Registration", text, re.I):
        score += 3
        reason.append("외국인등록증")
    if find_reg_no(lines):
        score += 3
        reason.append("номер ARC")
    if re.search(r"체류자격|Status|\([A-H]-\d", text):
        score += 1
    return score, ", ".join(reason)


def classify_photo(lines: List[str]) -> Tuple[str, str]:
    """(тип, как определен). Тип '' — не удалось определить."""
    scores = {
        FRONT: front_score(lines),
        BACK: back_score(lines),
        BANK: bank_score(lines),
    }
    best = max(scores, key=lambda k: scores[k][0])
    value, reason = scores[best]
    if value < 3:
        return UNKNOWN, "не распознано"
    return best, f"OCR: {reason}" if reason else "OCR"
