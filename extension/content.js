// E9pay: заполнение формы заявки KICA (signra) данными клиента.
// Поля ищутся по подписям рядом с ними (이름, 주민등록번호, …), а не по внутренним id страницы,
// поэтому заполнение работает и на шаге 1, и на шаге 2 (после «확인»).
(() => {
  if (window.__e9payFill) return;

  const norm = (s) => (s || "").replace(/[\s✓✔*:·()\-]/g, "").toLowerCase();
  const LABELS = {
    name: ["이름", "성명"],
    rrn: ["주민등록번호", "외국인등록번호"],
    temp: ["발급용임시번호", "임시번호"],
    mobile: ["휴대폰번호", "휴대전화", "휴대폰"],
    country: ["출신국가", "국적"],
    address: ["주소"],
    email: ["email", "이메일"],
  };
  const ALL_LABELS = Object.values(LABELS).flat().concat(["전화번호"]);
  const isLabelText = (t) => ALL_LABELS.some((l) => t.startsWith(norm(l)));

  function usable(el) {
    if (!el || el.disabled) return false;
    if (el.tagName === "SELECT" || el.tagName === "TEXTAREA") return true;
    const t = (el.type || "text").toLowerCase();
    return !["hidden", "button", "submit", "checkbox", "radio", "image", "reset", "file"].includes(t);
  }

  function labelCells(names) {
    const wanted = names.map(norm);
    const out = [];
    for (const el of document.querySelectorAll("th, td, dt, label")) {
      const t = norm(el.textContent);
      if (!t || t.length > 24) continue;
      if (wanted.some((w) => t === w || (t.startsWith(w) && t.length <= w.length + 3))) out.push(el);
    }
    return out;
  }

  // Поля, относящиеся к подписи: ячейки справа до следующей подписи (+ строки ниже при rowspan).
  function fieldsFor(names) {
    for (const cell of labelCells(names)) {
      if (cell.tagName === "LABEL" && cell.htmlFor) {
        const el = document.getElementById(cell.htmlFor);
        if (usable(el)) return [el];
      }
      const fields = [];
      let sib = cell.nextElementSibling;
      while (sib) {
        if (sib.tagName === "TH" || (sib.tagName === "TD" && isLabelText(norm(sib.textContent)) &&
            !sib.querySelector("input,select,textarea"))) break;
        fields.push(...sib.querySelectorAll("input, select, textarea"));
        sib = sib.nextElementSibling;
      }
      const span = cell.rowSpan || 1;
      let row = cell.closest("tr");
      for (let i = 1; i < span && row; i++) {
        row = row.nextElementSibling;
        if (row) fields.push(...row.querySelectorAll("input, select, textarea"));
      }
      const good = fields.filter(usable);
      if (good.length) return good;
    }
    return [];
  }

  function setValue(el, value, onlyIfEmpty) {
    if (!el || value === undefined || value === null || value === "") return false;
    if (onlyIfEmpty && el.tagName !== "SELECT" && el.value) return false;
    if (el.tagName === "SELECT") {
      const opt = [...el.options].find((o) => o.value === value || o.text.trim() === value);
      if (!opt) return false;
      el.value = opt.value;
    } else {
      const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(proto, "value").set.call(el, value);
    }
    for (const type of ["input", "change", "keyup", "blur"]) el.dispatchEvent(new Event(type, { bubbles: true }));
    el.style.outline = "2px solid #F5C518";
    el.style.backgroundColor = "#FFF8D6";
    return true;
  }

  const texts = (fields) => fields.filter((f) => f.tagName !== "SELECT");
  const selects = (fields) => fields.filter((f) => f.tagName === "SELECT");

  function fill(d, onlyIfEmpty) {
    const done = [], missing = [];
    const mark = (label, ok) => (ok ? done : missing).push(label);
    let f;

    f = texts(fieldsFor(LABELS.name));
    if (f.length) mark("이름", setValue(f[0], d.name, onlyIfEmpty));

    f = texts(fieldsFor(LABELS.rrn));
    if (f.length >= 2) {
      mark("주민등록번호", setValue(f[0], d.rrn1, onlyIfEmpty) | setValue(f[1], d.rrn2, onlyIfEmpty));
    } else if (f.length === 1) {
      mark("주민등록번호", setValue(f[0], d.rrn1 + d.rrn2, onlyIfEmpty));
    }

    f = texts(fieldsFor(LABELS.temp));
    if (f.length) mark("발급용 임시번호", setValue(f[0], d.temp_number, onlyIfEmpty));

    f = fieldsFor(LABELS.mobile);
    if (f.length) {
      const [s] = selects(f), t = texts(f);
      let ok = false;
      if (d.phone && d.phone[1]) {
        if (s) ok = setValue(s, d.phone[0], false) || ok;
        if (t.length >= 2) ok = setValue(t[0], d.phone[1], onlyIfEmpty) | setValue(t[1], d.phone[2], onlyIfEmpty) || ok;
        else if (t.length === 1) ok = setValue(t[0], d.phone.join("-"), onlyIfEmpty) || ok;
      }
      mark("휴대폰 번호", ok);
    }

    f = texts(fieldsFor(LABELS.country));
    if (f.length) mark("출신 국가", setValue(f[0], d.country_ko || d.country_en, onlyIfEmpty));

    f = texts(fieldsFor(LABELS.address));
    if (f.length) {
      // первое короткое поле — почтовый индекс (заполняется через «우편번호 찾기»)
      const first = f[0];
      const isZip = (first.maxLength > 0 && first.maxLength <= 7) || first.size <= 7 || f.length >= 3;
      const target = isZip ? f[1] : f[0];
      mark("주소", setValue(target, d.address, onlyIfEmpty));
    }

    f = fieldsFor(LABELS.email);
    if (f.length) {
      const t = texts(f), [s] = selects(f);
      let ok = false;
      if (d.email_local) {
        if (t.length >= 2) {
          if (s) setValue(s, "직접입력", false) || setValue(s, "direct", false);
          ok = setValue(t[0], d.email_local, onlyIfEmpty) | setValue(t[1], d.email_domain, onlyIfEmpty);
        } else if (t.length === 1) ok = setValue(t[0], `${d.email_local}@${d.email_domain}`, onlyIfEmpty);
      }
      mark("E-mail", !!ok);
    }
    return { done, missing };
  }

  // После выбора клиента следим за страницей: на шаге 2 появятся новые поля — заполним пустые.
  let current = null, observer = null, until = 0;
  function watch() {
    if (observer) observer.disconnect();
    let timer = null;
    observer = new MutationObserver(() => {
      if (!current || Date.now() > until) { observer.disconnect(); return; }
      clearTimeout(timer);
      timer = setTimeout(() => fill(current, true), 300);
    });
    observer.observe(document.body, { childList: true, subtree: true, attributes: true,
                                      attributeFilter: ["style", "class"] });
  }

  window.__e9payFill = (data) => {
    current = data;
    until = Date.now() + 15 * 60 * 1000;
    const res = fill(data, false);
    watch();
    return res;
  };

  if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.onMessage) {
    chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
      if (msg && msg.type === "e9pay-fill") {
        reply(window.__e9payFill(msg.data));
      }
    });
  }
})();
