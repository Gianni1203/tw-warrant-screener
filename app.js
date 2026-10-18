(() => {
  "use strict";

  const PAGE = 300;
  const STORE_KEY = "warrant-filters-v1";
  const PRESET = { cp: "C", slMax: "1", daysMin: "60", mnyMin: "-15", mnyMax: "5", needQuote: true };

  // 表格欄位：key 對應 data/warrants.json 的欄位，fmt 為顯示方式
  const COLS = [
    { key: "code", label: "代號", cls: "code" },
    { key: "name", label: "名稱", cls: "name" },
    { key: "uName", label: "標的", fmt: (r) => `${r.uName || ""}<span class="sub">${r.uCode || ""}</span>` },
    { key: "cp", label: "類型", fmt: (r) => `<span class="cp-${r.cp}">${r.cp === "C" ? "認購" : "認售"}</span>` },
    { key: "sl", label: "差槓比", cls: "sl", fmt: (r) => n(r.sl, 3) },
    { key: "spread", label: "價差比%", fmt: (r) => n(r.spread, 2) },
    { key: "lev", label: "實際槓桿", fmt: (r) => n(r.lev, 2) },
    { key: "moneyness", label: "價內外%", fmt: (r) => n(r.moneyness, 1) },
    { key: "days", label: "到期日", fmt: (r) => `${r.expiry || "–"}<span class="sub">剩 ${r.days ?? "–"} 天</span>` },
    { key: "ratio", label: "行使比例", fmt: (r) => n(r.ratio, 4) },
    { key: "bid", label: "買 / 賣", fmt: (r) => `${n(r.bid, 2)} / ${n(r.ask, 2)}` },
    { key: "S", label: "標的價", fmt: (r) => n(r.S, 2) },
    { key: "K", label: "履約價", fmt: (r) => n(r.K, 2) },
    { key: "iv", label: "IV%", fmt: (r) => n(r.iv, 1) },
    { key: "vol", label: "成交張", fmt: (r) => (r.vol ? r.vol.toLocaleString() : "0") },
  ];

  const $ = (s) => document.querySelector(s);
  const form = $("#filters");
  let ALL = [];
  let view = [];
  let shown = PAGE;
  let sort = { key: "sl", desc: false };
  let asof = null;

  function n(v, d) {
    return v === null || v === undefined ? "–" : Number(v).toFixed(d);
  }
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  // ---------- 篩選條件存取 ----------
  function readForm() {
    const fd = new FormData(form);
    const o = Object.fromEntries(fd.entries());
    o.needQuote = form.needQuote.checked;
    o.needVol = form.needVol.checked;
    return o;
  }
  function writeForm(o) {
    for (const el of form.elements) {
      if (!el.name || !(el.name in o)) continue;
      if (el.type === "checkbox") el.checked = !!o[el.name];
      else if (el.type === "radio") el.checked = el.value === o[el.name];
      else el.value = o[el.name];
    }
  }
  function save(o) {
    try { localStorage.setItem(STORE_KEY, JSON.stringify(o)); } catch (_) { /* 無痕模式等情況忽略 */ }
  }
  function load() {
    try { return JSON.parse(localStorage.getItem(STORE_KEY) || "null"); } catch (_) { return null; }
  }

  // ---------- 篩選與排序 ----------
  const numOr = (v) => (v === "" || v === undefined ? null : Number(v));

  function apply() {
    const f = readForm();
    save(f);
    const q = (f.under || "").trim().toLowerCase();
    const slMax = numOr(f.slMax), dMin = numOr(f.daysMin), dMax = numOr(f.daysMax);
    const rMin = numOr(f.ratioMin), rMax = numOr(f.ratioMax);
    const mMin = numOr(f.mnyMin), mMax = numOr(f.mnyMax), levMin = numOr(f.levMin);

    view = ALL.filter((r) => {
      if (f.cp !== "all" && r.cp !== f.cp) return false;
      if (f.mkt !== "all" && r.mkt !== f.mkt) return false;
      if (q && !(String(r.uCode || "").toLowerCase().startsWith(q) || String(r.uName || "").toLowerCase().includes(q)
                 || r.code.toLowerCase().startsWith(q) || r.name.toLowerCase().includes(q))) return false;
      if (f.needQuote && !(r.bid && r.ask)) return false;
      if (f.needVol && !r.vol) return false;
      if (slMax !== null && !(r.sl !== null && r.sl <= slMax)) return false;
      if (dMin !== null && !(r.days !== null && r.days >= dMin)) return false;
      if (dMax !== null && !(r.days !== null && r.days <= dMax)) return false;
      if (rMin !== null && !(r.ratio !== null && r.ratio >= rMin)) return false;
      if (rMax !== null && !(r.ratio !== null && r.ratio <= rMax)) return false;
      if (mMin !== null && !(r.moneyness !== null && r.moneyness >= mMin)) return false;
      if (mMax !== null && !(r.moneyness !== null && r.moneyness <= mMax)) return false;
      if (levMin !== null && !(r.lev !== null && r.lev >= levMin)) return false;
      return true;
    });
    doSort();
    shown = PAGE;
    updateDateHint(dMin, dMax);
    render();
  }

  function doSort() {
    const { key, desc } = sort;
    const dir = desc ? -1 : 1;
    view.sort((a, b) => {
      const x = a[key], y = b[key];
      if (x === null || x === undefined) return 1;   // 空值永遠排最後
      if (y === null || y === undefined) return -1;
      if (typeof x === "string") return x.localeCompare(y, "zh-Hant") * dir;
      return (x - y) * dir;
    });
  }

  function updateDateHint(dMin, dMax) {
    if (!asof || (dMin === null && dMax === null)) { $("#dateHint").textContent = ""; return; }
    const d = (k) => { const t = new Date(asof); t.setDate(t.getDate() + k); return t.toISOString().slice(0, 10); };
    $("#dateHint").textContent = `到期日 ${dMin !== null ? d(dMin) : "不限"} ～ ${dMax !== null ? d(dMax) : "不限"}`;
  }

  // ---------- 畫面 ----------
  // 標題列只建立一次，之後只更新排序標記（避免點擊途中元素被替換而丟失點擊）
  $("#tbl thead tr").innerHTML = COLS.map((c) => `<th data-key="${c.key}" scope="col">${c.label}</th>`).join("");
  function renderHead() {
    for (const th of document.querySelectorAll("#tbl thead th")) {
      const key = th.dataset.key === "bid" ? "ask" : th.dataset.key;
      const on = key === sort.key;
      th.classList.toggle("sorted", on);
      th.classList.toggle("desc", on && sort.desc);
    }
  }

  function render() {
    renderHead();
    $("#count").textContent = `符合 ${view.length.toLocaleString()} 檔 / 共 ${ALL.length.toLocaleString()} 檔`;
    const body = $("#tbl tbody");
    if (!view.length) {
      body.innerHTML = `<tr><td class="empty" colspan="${COLS.length}">沒有符合條件的權證，試著放寬條件</td></tr>`;
      $("#more").hidden = true;
      return;
    }
    body.innerHTML = view.slice(0, shown).map((r) =>
      "<tr>" + COLS.map((c) => `<td class="${c.cls || ""}">${c.fmt ? c.fmt(r) : esc(r[c.key])}</td>`).join("") + "</tr>"
    ).join("");
    $("#more").hidden = view.length <= shown;
    $("#more").textContent = `顯示更多（還有 ${(view.length - shown).toLocaleString()} 檔）`;
  }

  // ---------- 事件 ----------
  let timer;
  form.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(apply, 150); });
  form.addEventListener("change", apply);
  form.addEventListener("reset", () => setTimeout(apply, 0));
  $("#preset").addEventListener("click", () => { form.reset(); writeForm(PRESET); apply(); });
  $("#more").addEventListener("click", () => { shown += PAGE; render(); });
  $("#tbl thead").addEventListener("click", (e) => {
    const th = e.target.closest("th");
    if (!th) return;
    const key = th.dataset.key === "bid" ? "ask" : th.dataset.key;
    sort = { key, desc: sort.key === key ? !sort.desc : false };
    doSort();
    render();
  });

  // ---------- 載入資料 ----------
  fetch("data/warrants.json", { cache: "no-cache" })
    .then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); })
    .then((d) => {
      asof = d.asof;
      ALL = d.rows.map((row) => Object.fromEntries(d.fields.map((f, i) => [f, row[i]])));
      $("#status").textContent = `資料日期 ${d.asof}・更新於 ${d.updated.replace("T", " ")}`
        + (d.demo ? "・⚠ 目前為示範資料，等第一次自動更新後會換成真實資料" : "")
        + (d.notes && d.notes.length ? "・⚠ " + d.notes.join("；") : "");
      const saved = load();
      if (saved) writeForm(saved);
      apply();
    })
    .catch((e) => {
      $("#status").textContent = `無法載入資料（${e.message}）。請確認 data/warrants.json 存在，並透過網址開啟而非直接點開檔案。`;
    });
})();
