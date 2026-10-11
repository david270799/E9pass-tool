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

    // Страна и адрес на сайте выбираются только в окнах поиска — их заполняем отдельно (pickers).
    if (texts(fieldsFor(LABELS.country)).length) mark("출신 국가", !!(d.country_ko || d.country_en));
    if (texts(fieldsFor(LABELS.address)).length) mark("주소", !!(d.address_keywords || []).length);

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

  // ---------------------------------------------------------------- окна поиска сайта
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  // offsetParent у окон с position:fixed всегда null — смотрим на реальные размеры и стили
  const visible = (el) => {
    if (!el || !el.getClientRects().length) return false;
    const st = getComputedStyle(el);
    return st.display !== "none" && st.visibility !== "hidden";
  };

  async function waitFor(fn, timeout = 6000, step = 150) {
    const end = Date.now() + timeout;
    while (Date.now() < end) {
      const v = fn();
      if (v) return v;
      await sleep(step);
    }
    return null;
  }

  function clickEl(el) {
    for (const type of ["mousedown", "mouseup", "click"]) {
      el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
    }
  }

  // кнопка «국가 찾기» / «우편번호 찾기» в строке поля
  function findButton(labelNames, text) {
    for (const cell of labelCells(labelNames)) {
      const rows = [cell.closest("tr")];
      for (let i = 1, r = rows[0]; i < (cell.rowSpan || 1) && r; i++) { r = r.nextElementSibling; rows.push(r); }
      for (const row of rows.filter(Boolean)) {
        const b = [...row.querySelectorAll("button, a, span, input[type=button]")]
          .find((e) => norm(e.textContent || e.value).includes(norm(text)));
        if (b) return b;
      }
    }
    return [...document.querySelectorAll("button, a, span, input[type=button]")]
      .find((e) => norm(e.textContent || e.value) === norm(text)) || null;
  }

  function resultLinks(layer) {
    const box = layer.querySelector(".find-zip-result, .tbl-style2") || layer;
    return [...box.querySelectorAll("tbody tr")].filter((tr) => !tr.querySelector("th"));
  }

  async function searchInLayer(layer, inputSel, keyword) {
    const input = layer.querySelector(inputSel);
    if (!input) return [];
    const before = (layer.querySelector(".find-zip-result") || layer).innerText;
    setValue(input, keyword, false);
    input.style.outline = "";
    input.style.backgroundColor = "";
    const btn = input.form && (input.form.querySelector(".btn-cmn, .btn1, button, input[type=submit]"));
    if (btn) clickEl(btn);
    else input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", keyCode: 13, bubbles: true }));
    await waitFor(() => (layer.querySelector(".find-zip-result") || layer).innerText !== before, 5000);
    await sleep(250);
    return resultLinks(layer).filter((tr) => !/입력하십시오|입력하세요|없습니다/.test(tr.innerText));
  }

  async function pickCountry(d) {
    const keyword = d.country_ko || d.country_en;
    const field = texts(fieldsFor(LABELS.country))[0];
    if (!keyword || !field || field.value) return field && field.value ? "уже выбрана" : "";
    const btn = findButton(LABELS.country, "국가 찾기");
    if (!btn) { setValue(field, `${d.country_ko}(${d.country_en})`, true); return "вписана вручную"; }
    clickEl(btn);
    const layer = await waitFor(() => { const l = document.getElementById("findNationLayer"); return visible(l) && l; });
    if (!layer) return "окно не открылось";
    for (const kw of [d.country_ko, d.country_en].filter(Boolean)) {
      const rows = await searchInLayer(layer, "#nationKeyWord, input[name=nationKeyWord], input[type=text]", kw);
      // точное совпадение по корейскому названию: «우즈베키스탄(Uzbekistan)»
      const row = rows.find((tr) => tr.innerText.replace(/\s/g, "").startsWith((d.country_ko || "").replace(/\s/g, "")) && d.country_ko)
        || rows.find((tr) => (d.country_en && tr.innerText.toLowerCase().includes(d.country_en.toLowerCase())));
      if (row) {
        clickEl(row.querySelector("a") || row.querySelector("td") || row);
        await waitFor(() => !visible(layer) || field.value, 3000);
        return field.value ? "выбрана" : "выбрана в окне";
      }
    }
    return "не найдена — выберите в окне";
  }

  function addressScore(text, address) {
    const tokens = (address || "").split(/[\s,]+/).filter((t) => t.length >= 2);
    const t = text.replace(/\s+/g, " ");
    return tokens.reduce((s, tok) => s + (t.includes(tok) ? tok.length : 0), 0);
  }

  async function pickAddress(d) {
    const fields = texts(fieldsFor(LABELS.address));
    const zip = fields[0];
    if (!fields.length || !(d.address_keywords || []).length) return "";
    if (zip && zip.value) return "уже выбран";
    const btn = findButton(LABELS.address, "우편번호 찾기");
    if (!btn) return "кнопка не найдена";
    clickEl(btn);
    const layer = await waitFor(() => { const l = document.getElementById("findZipLayer"); return visible(l) && l; });
    if (!layer) return "окно не открылось";
    for (const kw of d.address_keywords) {
      const rows = await searchInLayer(layer, "#addrKeyWord, input[name=addrKeyWord], input[type=text]", kw);
      if (!rows.length) continue;
      // лучшая строка: больше всего совпадений с адресом клиента; щелкаем «신주소» (или «구주소»)
      let best = null, bestScore = -1;
      for (const tr of rows) {
        for (const a of tr.querySelectorAll("a, td")) {
          const sc = addressScore(a.innerText, d.address) + (/신주소/.test(a.innerText) ? 1 : 0);
          if (a.tagName === "A" && sc > bestScore) { best = a; bestScore = sc; }
        }
        if (!best) { const sc = addressScore(tr.innerText, d.address); if (sc > bestScore) { best = tr; bestScore = sc; } }
      }
      if (best) {
        clickEl(best);
        await waitFor(() => !visible(layer) || (zip && zip.value), 3000);
        const detail = fields[fields.length - 1];
        if (d.address_detail && detail && detail !== zip && !detail.value) setValue(detail, d.address_detail, true);
        return "выбран";
      }
    }
    return "не найден — выберите в окне (адрес в буфере, Ctrl+V)";
  }

  let picking = false;
  const picked = { country: false, address: false };
  const status = {};          // для диагностики: window.__e9payStatus
  window.__e9payStatus = status;
  async function runPickers(d) {
    if (picking) return;
    picking = true;
    try {
      // только когда поля уже видны (шаг 2 после «확인»), иначе подождем следующего изменения страницы
      const ctry = texts(fieldsFor(LABELS.country))[0];
      if (!picked.country && visible(ctry)) { picked.country = true; status.country = await pickCountry(d); }
      const addr = texts(fieldsFor(LABELS.address))[0];
      if (!picked.address && visible(addr)) { picked.address = true; status.address = await pickAddress(d); }
    } finally { picking = false; }
  }

  // После выбора клиента следим за страницей: на шаге 2 появятся новые поля — заполним пустые.
  let current = null, observer = null, until = 0;
  function watch() {
    if (observer) observer.disconnect();
    let timer = null;
    observer = new MutationObserver(() => {
      if (!current || Date.now() > until) { observer.disconnect(); return; }
      clearTimeout(timer);
      timer = setTimeout(() => { fill(current, true); runPickers(current); }, 300);
    });
    observer.observe(document.body, { childList: true, subtree: true, attributes: true,
                                      attributeFilter: ["style", "class"] });
  }

  window.__e9payFill = (data) => {
    current = data;
    picked.country = picked.address = false;
    until = Date.now() + 15 * 60 * 1000;
    const res = fill(data, false);
    watch();
    runPickers(data);
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
