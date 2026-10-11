"""Связь с расширением Chrome для автозаполнения форм (сайт KICA / signra).

Пока E9pay запущена, она слушает адрес 127.0.0.1 (только этот компьютер, из сети недоступен)
и отвечает ТОЛЬКО расширению E9pay (проверка Origin = chrome-extension://<наш id>).
Данные никуда не отправляются — расширение вставляет их в форму на открытой странице.
"""

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import parse_qs, urlparse

from .db import Database
from .ocr.front import COUNTRIES_KO, normalize_name

log = logging.getLogger("e9pay")

PORTS = (47821, 47822, 47823)
# id расширения задается ключом в extension/manifest.json и поэтому одинаков на всех компьютерах
EXTENSION_ID = "poihmknhhlkbkghkhcgepgmhjaoelgjk"
ALLOWED_ORIGINS = {f"chrome-extension://{EXTENSION_ID}"}
DEFAULT_TEMP_NUMBER = "112233"   # «발급용 임시번호» — 6 цифр, которые клиент «придумывает» сам

_EN_TO_KO = {}
for ko, en in COUNTRIES_KO.items():
    _EN_TO_KO.setdefault(en, ko)


def form_data(c, temp_number: str = DEFAULT_TEMP_NUMBER) -> dict:
    """Поля формы KICA для клиента."""
    digits = "".join(ch for ch in c.arc_number if ch.isdigit())
    phone = "".join(ch for ch in (c.phone or "") if ch.isdigit())
    if len(phone) == 11:
        phone_parts = [phone[:3], phone[3:7], phone[7:]]
    elif len(phone) == 10:
        phone_parts = [phone[:3], phone[3:6], phone[6:]]
    else:
        phone_parts = ["", "", ""]
    email = (c.email or "").strip().lower()
    local, _, domain = email.partition("@")
    return {
        "id": c.id,
        "name": normalize_name(c.full_name).lower(),        # сайт принимает имя строчными буквами
        "rrn1": digits[:6], "rrn2": digits[6:13],            # 주민등록번호: дата рождения + 7 цифр ARC
        "temp_number": temp_number,
        "phone": phone_parts,
        "country_en": c.nationality,
        "country_ko": _EN_TO_KO.get((c.nationality or "").upper(), ""),
        "address": c.registered_address,
        "email_local": local, "email_domain": domain,
    }


def client_list(db: Database, period: Optional[str]) -> list:
    periods = [period] if period else db.periods()[:3]
    out = []
    for p in periods:
        for c in db.clients(p):
            out.append({"id": c.id, "name": c.full_name, "arc": c.arc_number, "period": c.batch_period,
                        "phone": c.phone, "email": c.email, "status": c.status})
    return out


class _Handler(BaseHTTPRequestHandler):
    db: Database = None
    get_period = staticmethod(lambda: None)
    get_temp = staticmethod(lambda: DEFAULT_TEMP_NUMBER)

    def log_message(self, *_args):  # не пишем запросы в консоль
        pass

    def _origin_ok(self) -> bool:
        """Только наше расширение.

        Chrome не всегда добавляет Origin к запросам расширения с host_permissions, поэтому
        расширение шлет свой заголовок X-E9pay. Веб-страница не может отправить такой заголовок
        без предварительного запроса OPTIONS со своим Origin — а его мы отклоняем.
        """
        origin = self.headers.get("Origin", "")
        if origin:
            return origin in ALLOWED_ORIGINS
        return self.headers.get("X-E9pay") == "1"

    def _send(self, code: int, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        origin = self.headers.get("Origin", "")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        if not self._origin_ok():
            self.send_response(403)
            self.end_headers()
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", self.headers.get("Origin"))
        self.send_header("Access-Control-Allow-Methods", "GET")
        self.send_header("Access-Control-Allow-Headers", "X-E9pay")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.end_headers()

    def do_GET(self):
        if not self._origin_ok():
            self._send(403, {"error": "forbidden"})
            return
        url = urlparse(self.path)
        q = parse_qs(url.query)
        try:
            if url.path == "/ping":
                self._send(200, {"app": "E9pay", "period": self.get_period()})
            elif url.path == "/clients":
                period = q.get("period", [self.get_period()])[0] or None
                self._send(200, {"period": period, "clients": client_list(self.db, period)})
            elif url.path.startswith("/client/"):
                c = self.db.client(int(url.path.rsplit("/", 1)[1]))
                if not c:
                    self._send(404, {"error": "not found"})
                else:
                    self._send(200, form_data(c, self.get_temp()))
            else:
                self._send(404, {"error": "unknown"})
        except Exception as e:  # noqa: BLE001
            log.exception("bridge error")
            self._send(500, {"error": str(e)})


_server: Optional[ThreadingHTTPServer] = None


def start(db: Database, get_period=lambda: None, get_temp=lambda: DEFAULT_TEMP_NUMBER) -> Optional[int]:
    """Запускает мост в фоне. Возвращает порт или None, если все порты заняты."""
    global _server
    handler = type("Handler", (_Handler,), {"db": db, "get_period": staticmethod(get_period),
                                            "get_temp": staticmethod(get_temp)})
    for port in PORTS:
        try:
            _server = ThreadingHTTPServer(("127.0.0.1", port), handler)
        except OSError:
            continue
        threading.Thread(target=_server.serve_forever, daemon=True, name="e9pay-bridge").start()
        log.info("мост для расширения Chrome: 127.0.0.1:%d", port)
        return port
    log.warning("мост для расширения Chrome не запущен: порты заняты")
    return None


def stop():
    if _server:
        _server.shutdown()
