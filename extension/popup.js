// Окно расширения: список клиентов из программы E9pay (127.0.0.1, только этот компьютер).
const PORTS = [47821, 47822, 47823];
const HDR = { "X-E9pay": "1" };
let base = null, clients = [], used = {};

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s || "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

async function connect() {
  for (const p of PORTS) {
    try {
      const r = await fetch(`http://127.0.0.1:${p}/ping`, { cache: "no-store", headers: HDR });
      if (r.ok) { base = `http://127.0.0.1:${p}`; return await r.json(); }
    } catch (_) { /* следующий порт */ }
  }
  return null;
}

const usedKey = (c) => `${c.arc || c.id}`;

function render() {
  const q = $("search").value.trim().toLowerCase();
  const shown = clients
    .filter((c) => !q || (c.name || "").toLowerCase().includes(q) || (c.arc || "").includes(q))
    // уже использованные — вниз списка
    .sort((a, b) => (used[usedKey(a)] ? 1 : 0) - (used[usedKey(b)] ? 1 : 0));
  $("list").innerHTML = shown.map((c) => {
    const u = used[usedKey(c)];
    return `
    <button class="c${u ? " used" : ""}" data-id="${c.id}" data-key="${esc(usedKey(c))}">
      <b>${u ? "✓ " : ""}${esc(c.name || "без имени")}</b>
      <small>${esc(c.arc)} · ${esc(c.period)}${u ? " · использован " + esc(u) : ""}</small>
      ${c.phone && c.email ? "" : '<br><small class="warn">нет телефона или e-mail</small>'}
    </button>`;
  }).join("") || '<div id="status">Клиентов не найдено</div>';
}

async function fillTab(data) {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  // на всякий случай внедряем скрипт заполнения (если страница открылась до установки расширения)
  try {
    await chrome.scripting.executeScript({ target: { tabId: tab.id, allFrames: true }, files: ["content.js"] });
  } catch (_) { /* служебная страница Chrome — заполнять нечего */ }
  const results = await chrome.scripting.executeScript({
    target: { tabId: tab.id, allFrames: true },
    func: (d) => (window.__e9payFill ? window.__e9payFill(d) : null),
    args: [data],
  });
  const done = new Set(), missing = new Set();
  for (const r of results) {
    if (r && r.result) { r.result.done.forEach((x) => done.add(x)); r.result.missing.forEach((x) => missing.add(x)); }
  }
  return { done: [...done], missing: [...missing].filter((x) => !done.has(x)) };
}

async function choose(id, key) {
  const r = await fetch(`${base}/client/${id}`, { cache: "no-store", headers: HDR });
  const data = await r.json();
  // адрес — в буфер обмена на случай, если в окне «우편번호 찾기» придется выбрать вручную (Ctrl+V)
  try { if (data.address) await navigator.clipboard.writeText(data.address); } catch (_) { /* не критично */ }
  try {
    await fillTab(data);
  } catch (e) {
    $("status").innerHTML = `<span class="warn">Не удалось заполнить: ${esc(String(e))}</span>`;
    return;
  }
  // отметка «использован» — клиент уходит вниз списка
  used[key] = new Date().toLocaleDateString("ru-RU");
  await chrome.storage.local.set({ used });
  window.close();
}

(async () => {
  const info = await connect();
  if (!info) {
    $("status").innerHTML = '<span class="warn">Программа E9pay не запущена.</span> Откройте E9pay и нажмите на расширение еще раз.';
    $("search").style.display = "none";
    return;
  }
  const r = await fetch(`${base}/clients`, { cache: "no-store", headers: HDR });
  const data = await r.json();
  clients = data.clients || [];
  used = (await chrome.storage.local.get("used")).used || {};
  $("status").textContent = `Период ${data.period || "—"} · клиентов: ${clients.length}`;
  render();
  $("search").addEventListener("input", render);
  $("search").focus();
  $("list").addEventListener("click", (e) => {
    const b = e.target.closest(".c");
    if (b) choose(b.dataset.id, b.dataset.key);
  });
})();
