"use strict";
/* Bill-pay chat client. Every word the agent says comes from the server (the orchestrator);
   this file only draws it: bubbles, options to tap, the confirmation card, the PIN sheet, receipts
   and the «what I checked» expander. A tapped option sends only its id; the server decides what it means. */
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)");
  const wide = matchMedia("(min-width: 700px)");

  const store = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode: fine */ } },
  };

  function newSid() {
    try { if (crypto.randomUUID) return crypto.randomUUID(); } catch { /* insecure context */ }
    const a = new Uint8Array(16);
    crypto.getRandomValues(a);
    return [...a].map((b) => b.toString(16).padStart(2, "0")).join("");
  }

  const S = {
    sid: store.get("billpay.sid") || newSid(),
    config: { voice: false, pin_length: 4 },
    users: [],
    profile: null,
    demoPin: "",
    active: null, // the open card: {qid, el, data, deadline, timer}
    busy: false,
    seen: null, // quote ids already shown in the history list
    pin: null, // {qid, digits, busy}
    rec: null,
    fault: "none",
    lang: "iq",
  };
  store.set("billpay.sid", S.sid);

  const g = () => (S.profile && S.profile.gender === "m" ? "m" : "f");
  const T = {
    placeholder: { m: "اكتب طلبك…", f: "اكتبي طلبج…" },
    welcome: {
      m: "اكتب طلبك بالعراقي، وأني أحضّرلك بطاقة تأكيد. ما ينخصم ولا فلس إلا من تدوس «إي، ادفع» وتدخّل رمزك السري.",
      f: "اكتبي طلبج بالعراقي، وأني أحضّرلج بطاقة تأكيد. ما ينخصم ولا فلس إلا من تدوسين «إي، ادفع» وتدخّلين رمزج السري.",
    },
    pinTitle: { m: "دخّل الرمز السري", f: "دخّلي الرمز السري" },
    check: { m: "تأكد هسه", f: "تأكدي هسه" },
    expired: { m: "انتهت صلاحيتها. اكتب الطلب من جديد.", f: "انتهت صلاحيتها. اكتبي الطلب من جديد." },
    netErr: { m: "صار خلل بالاتصال. جرّب مرة ثانية.", f: "صار خلل بالاتصال. جرّبي مرة ثانية." },
    resetArm: { m: "متأكد؟ دوس مرة ثانية", f: "متأكدة؟ دوسي مرة ثانية" },
    micDenied: { m: "ما وصلت للمايكروفون. اكتب طلبك.", f: "ما وصلت للمايكروفون. اكتبي طلبج." },
    noRepeat: { m: "لا تعيدها، راح نتأكد", f: "لا تعيديها، راح نتأكد" },
    returned: { m: "رجعت الفلوس لمحفظتك", f: "رجعت الفلوس لمحفظتج" },
    nothingTaken: { m: "ما انخصم منك شي", f: "ما انخصم منج شي" },
  };
  // The conversation's language follows the agent's replies (reply.lang): Iraqi, MSA or English.
  // The card and receipt labels come from the server in that language; these are the page's own words.
  const LANG_UI = {
    iq: {
      placeholder: T.placeholder, pinTitle: T.pinTitle, expired: T.expired, netErr: T.netErr,
      closeText: "انلغت لأن انكتب طلب جديد", closeChange: "انلغت حتى نعدّلها", closeCancel: "انلغت",
      paid: "تم الدفع", failed: "ما تمت", checking: "قيد التأكد", wait: "لحظة…", dinar: "دينار",
      voiceReply: "رد صوتي", demoPin: "الرمز التجريبي", back: "رجوع",
      typing: "المساعد يكتب", delKey: "امسح رقم", yesPay: "إي، ادفع",
      dots: (n, L) => (n ? `دخلت ${n} من ${L}` : "ما دخلت أي رقم"),
    },
    msa: {
      placeholder: { m: "اكتب طلبك…", f: "اكتبي طلبك…" }, pinTitle: { m: "أدخل الرمز السري", f: "أدخلي الرمز السري" },
      expired: { m: "انتهت صلاحيتها. اكتب الطلب من جديد.", f: "انتهت صلاحيتها. اكتبي الطلب من جديد." },
      netErr: { m: "حدث خلل في الاتصال. جرّب مرة أخرى.", f: "حدث خلل في الاتصال. جرّبي مرة أخرى." },
      closeText: "أُلغيت لأنك كتبت طلباً جديداً", closeChange: "أُلغيت لنعدّلها", closeCancel: "أُلغيت",
      paid: "تم الدفع", failed: "لم تتم", checking: "قيد التأكد", wait: "لحظة…", dinar: "دينار",
      voiceReply: "رد صوتي", demoPin: "الرمز التجريبي", back: "رجوع",
      typing: "المساعد يكتب", delKey: "احذف رقماً", yesPay: "نعم، ادفع",
      dots: (n, L) => (n ? `أُدخل ${n} من ${L}` : "لم يُدخل أي رقم"),
    },
    en: {
      placeholder: { m: "Type your request…", f: "Type your request…" }, pinTitle: { m: "Enter your PIN", f: "Enter your PIN" },
      expired: { m: "It expired. Ask again.", f: "It expired. Ask again." },
      netErr: { m: "Connection problem. Try again.", f: "Connection problem. Try again." },
      closeText: "Cancelled: you wrote a new request", closeChange: "Cancelled so we can change it", closeCancel: "Cancelled",
      paid: "Paid", failed: "Not done", checking: "Checking", wait: "One moment…", dinar: "dinars",
      voiceReply: "Voice reply", demoPin: "Demo PIN", back: "Back",
      typing: "The assistant is typing", delKey: "Delete a digit", yesPay: "Yes, pay",
      dots: (n, L) => (n ? `${n} of ${L} entered` : "No digits entered"),
    },
  };
  const U = () => LANG_UI[S.lang] || LANG_UI.iq;
  const KIND = { transfer: "تحويل", bill: "فاتورة", topup: "شحن رصيد" };
  const KIND_ICON = { transfer: "i-transfer", bill: "i-bill", topup: "i-phone" };
  const STATUS = {
    succeeded: ["تمت", ""], reversed: ["رجعت الفلوس", "bad"], pending: ["قيد التنفيذ", "warn"], unknown: ["قيد التأكد", "warn"],
  };
  const FAULTS = [
    ["none", "عادي", "الدفعة تمشي طبيعي."],
    ["drop_after_commit", "يضيع الرد بعد الدفع", "المساعد يعيد المحاولة بنفس المفتاح، والفلوس تنخصم مرة وحدة بس."],
    ["biller_down", "الجهة ما ترد", "دفع الفاتورة أو الشحن يفشل، وما ينخصم شي."],
    ["network_down", "تنقطع الشبكة تماماً", "المساعد يگول بصراحة إنه لهسه ما يعرف، وبالرسالة الجاية يبلغ بالنتيجة."],
  ];

  const fmt = (n) => Number(n || 0).toLocaleString("en-US");
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const icon = (id) => `<svg aria-hidden="true"><use href="#${id}"/></svg>`;
  const num = (n) => `<span class="num">${fmt(n)}</span>`;
  const vibrate = (p) => { try { navigator.vibrate && navigator.vibrate(p); } catch { /* not supported */ } };
  let timeFmt = null;
  try {
    timeFmt = new Intl.DateTimeFormat("ar-IQ-u-nu-latn", { timeZone: "Asia/Baghdad", day: "numeric", month: "long", hour: "numeric", minute: "2-digit" });
  } catch { /* old browser */ }
  let dayFmt = null;
  try { dayFmt = new Intl.DateTimeFormat("ar-IQ-u-nu-latn", { timeZone: "Asia/Baghdad", day: "numeric", month: "long" }); } catch { /* old browser */ }
  const day = (iso) => {
    const d = new Date(iso || "");
    return isNaN(d) || !dayFmt ? "" : dayFmt.format(d);
  };
  const when = (iso) => {
    if (!iso) return "";
    const d = new Date(iso);
    return isNaN(d) || !timeFmt ? "" : timeFmt.format(d);
  };

  // ---------------------------------------------------------------- API

  async function api(path, { method = "GET", body, raw, type } = {}) {
    const headers = { "X-Demo-Session": S.sid };
    let payload;
    if (raw !== undefined) { payload = raw; headers["Content-Type"] = type || "application/octet-stream"; }
    else if (body !== undefined) { payload = JSON.stringify(body); headers["Content-Type"] = "application/json"; }
    const res = await fetch(path, { method, headers, body: payload });
    const data = await res.json().catch(() => null);
    if (!res.ok) { const err = new Error(`HTTP ${res.status}`); err.status = res.status; err.data = data; throw err; }
    return data;
  }

  // ---------------------------------------------------------------- thread

  const log = $("#log");
  const scroller = $("#scroller");
  const topbar = $("#topbar");

  const nearBottom = () => scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 160;
  function scrollToEnd() {
    requestAnimationFrame(() => scroller.scrollTo({ top: scroller.scrollHeight, behavior: reduceMotion.matches ? "auto" : "smooth" }));
  }
  function append(el, { force = false } = {}) {
    const stick = force || nearBottom();
    log.appendChild(el);
    if (stick) scrollToEnd();
    return el;
  }
  function bubble(role, text, { voice = false } = {}) {
    const el = document.createElement("div");
    el.className = `msg ${role}`;
    el.dir = "auto"; // English replies read left to right inside the Arabic page
    if (voice) el.innerHTML = `<span class="voice-tag" aria-label="رسالة صوتية">${icon("i-mic")}</span>`;
    el.appendChild(document.createTextNode(text));
    return el;
  }
  function showTyping() {
    const el = document.createElement("div");
    el.className = "typing";
    el.setAttribute("role", "status");
    el.setAttribute("aria-label", U().typing);
    el.innerHTML = "<span></span><span></span><span></span>";
    return append(el, { force: true });
  }

  scroller.addEventListener("scroll", () => {
    topbar.style.setProperty("--edge", scroller.scrollTop > 4 ? "1" : "0");
  }, { passive: true });

  // ---------------------------------------------------------------- the confirmation card

  let seq = 0;
  function renderCard(c) {
    const id = `card-${++seq}`;
    const el = document.createElement("article");
    el.className = "card";
    el.dataset.qid = c.quote_id;
    el.setAttribute("aria-labelledby", `${id}-t ${id}-p`);
    const L = { fee: "العمولة", no_fee: "بلا عمولة", total: "المجموع", balance_after: "الرصيد بعد الدفع", dinar: "دينار",
      flags: "تنبيهات", valid_note: "البطاقة صالحة خمس دقايق.", ...(c.labels || {}) };
    if (c.lang === "en") el.dir = "ltr";
    const cur = esc(L.dinar);
    const rows = (c.rows || []).map((r) => `<div><dt>${esc(r.label)}</dt><dd${r.ltr ? ' dir="ltr"' : ""}>${esc(r.value)}</dd></div>`);
    rows.push(`<div><dt>${esc(L.fee)}</dt><dd>${c.fee ? `${num(c.fee)} ${cur}` : esc(L.no_fee)}</dd></div>`);
    rows.push(`<div class="total"><dt>${esc(L.total)}</dt><dd>${num(c.total)} ${cur}</dd></div>`);
    if (c.balance_after !== null && c.balance_after !== undefined) rows.push(`<div><dt>${esc(L.balance_after)}</dt><dd>${num(c.balance_after)} ${cur}</dd></div>`);
    const flags = (c.flags || []).map((f) => `<li class="flag">${icon("i-warn")}<span>${esc(f)}</span></li>`).join("");
    const buttons = (c.buttons || []).map((b) =>
      `<button type="button" class="btn ${b.id === "confirm" ? "primary" : "quiet"}" data-action="${esc(b.id)}">${esc(b.label)}</button>`).join("");
    el.innerHTML = `
      <div class="card-progress" aria-hidden="true"><span></span></div>
      <div class="card-in">
        <div class="card-head">
          <span class="card-kind" id="${id}-t">${icon(KIND_ICON[c.kind] || "i-transfer")}${esc(c.title)}</span>
          <span class="timer" aria-hidden="true"></span>
        </div>
        <div class="card-payee" id="${id}-p">${esc(c.payee)}</div>
        <div class="amount"><span class="num">${fmt(c.amount)}</span><span class="cur">${cur}</span></div>
        ${c.amount_words ? `<div class="words">${esc(c.amount_words)}</div>` : ""}
        <dl class="rows">${rows.join("")}</dl>
        ${flags ? `<ul class="flags" aria-label="${esc(L.flags)}">${flags}</ul>` : ""}
        <p class="sr-only">${esc(L.valid_note)}</p>
        <div class="actions">${buttons}</div>
        <div class="card-status" role="status"></div>
      </div>`;
    return el;
  }

  function armCard(el, c) {
    const a = { qid: c.quote_id, el, data: c, deadline: Date.now() + (c.expires_in ?? 300) * 1000, total: 300 };
    S.active = a;
    const tick = () => {
      if (S.active !== a) return;
      const left = Math.max(0, Math.ceil((a.deadline - Date.now()) / 1000));
      const t = $(".timer", el);
      t.innerHTML = `${esc((c.labels && c.labels.valid) || "صالحة")} <span class="num">${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}</span>`;
      t.classList.toggle("low", left <= 60);
      $(".card-progress span", el).style.transform = `scaleX(${Math.min(1, left / a.total)})`;
      if (left <= 0) closeCard(a.qid, U().expired[g()], "warn", "i-clock");
    };
    tick();
    a.timer = setInterval(tick, 1000);
  }

  function closeCard(qid, label, tone = "", ic = "i-x") {
    if (S.active && S.active.qid === qid) { clearInterval(S.active.timer); S.active = null; }
    for (const el of $$(`.card[data-qid="${CSS.escape(qid)}"]`)) {
      el.classList.remove("ok", "bad", "warn");
      el.classList.add("closed");
      if (tone) el.classList.add(tone);
      $(".card-status", el).innerHTML = `${icon(ic)}<span>${esc(label)}</span>`;
      $$("button", el).forEach((b) => { b.disabled = true; });
      $(".timer", el).textContent = "";
    }
  }

  // How a closed card is labelled; read when it closes, so it is in the conversation's language then.
  const CLOSE_AS = {
    get text() { return [U().closeText, "", "i-x"]; },
    get change() { return [U().closeChange, "", "i-x"]; },
    get cancel() { return [U().closeCancel, "", "i-x"]; },
  };
  // ---------------------------------------------------------------- options to tap, and «what I checked»

  const WHY_ID = "opt_why"; // «how did you check?» is the expander under the message here, not a chip
  const SHOWS_CHECKS = new Set(["card", "refuse", "done", "failed", "unknown"]);

  function renderOptions(reply) {
    const list = (reply.options || []).filter((o) => o.id !== WHY_ID);
    if (!list.length) return null;
    const el = document.createElement("div");
    el.className = `options ${reply.options_role === "next" ? "next" : "answer"}`;
    el.setAttribute("role", "group");
    if (reply.options_title) el.setAttribute("aria-label", reply.options_title);
    if (reply.lang === "en") el.dir = "ltr";
    for (const o of list) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "option";
      b.dataset.option = o.id;
      b.innerHTML = `<b>${esc(o.title)}</b>${o.description ? `<small>${esc(o.description)}</small>` : ""}`;
      el.appendChild(b);
    }
    return el;
  }

  function renderChecks(reply) {
    const list = reply.evidence || [];
    if (!list.length || !reply.why_label || !SHOWS_CHECKS.has(reply.kind)) return null;
    const el = document.createElement("details");
    el.className = "checks";
    if (reply.lang === "en") el.dir = "ltr";
    const items = list.map((c) => `<li><span>${esc(c.line)}</span><small>${esc(c.from)}</small></li>`).join("");
    el.innerHTML = `<summary>${icon("i-q")}<span>${esc(reply.why_label)}</span></summary><ul>${items}</ul>`;
    return el;
  }

  // The answers to an earlier question cannot be tapped once a newer reply is in (the server would call them old).
  function retireAnswers() {
    $$(".options.answer .option", log).forEach((b) => { b.disabled = true; });
  }

  const receiptClose = (status) => ({
    succeeded: [U().paid, "ok", "i-check"],
    failed: [U().failed, "bad", "i-x"],
    unknown: [U().checking, "warn", "i-q"],
  }[status] || CLOSE_AS.cancel);

  // ---------------------------------------------------------------- receipts

  function renderReceipt(r) {
    const el = document.createElement("article");
    const tone = { succeeded: "ok", failed: "bad", unknown: "unknown" }[r.status] || "unknown";
    el.className = `receipt ${tone}`;
    el.dataset.qid = r.quote_id;
    if (r.lang === "en") el.dir = "ltr";
    const L = {
      succeeded: "تمت العملية", failed: "ما تمت العملية", unknown: "لهسه ما نعرف النتيجة", with_fee: "مع العمولة",
      balance_now: "الرصيد هسه", time: "الوقت", reference: "رقم العملية", dinar: "دينار",
      returned: T.returned[g()], nothing_taken: T.nothingTaken[g()], no_repeat: T.noRepeat[g()], check: T.check[g()],
      ...(r.labels || {}),
    };
    const title = esc(L[r.status] || L.unknown);
    const cur = esc(L.dinar);
    const ic = { succeeded: "i-check", failed: "i-x", unknown: "i-q" }[r.status];
    const sub = [r.payee, r.period].filter(Boolean).join(" · ");
    const money = r.status === "failed" ? (r.money_returned ? L.returned : L.nothing_taken) : L.no_repeat;
    const rows = [];
    if (r.status === "succeeded") {
      if (r.fee) rows.push(`<div><dt>${esc(L.with_fee)}</dt><dd>${num(r.total)} ${cur}</dd></div>`);
      if (r.balance_after !== null && r.balance_after !== undefined) rows.push(`<div><dt>${esc(L.balance_now)}</dt><dd>${num(r.balance_after)} ${cur}</dd></div>`);
      if (r.completed_at) rows.push(`<div><dt>${esc(L.time)}</dt><dd>${esc(when(r.completed_at))}</dd></div>`);
      if (r.reference) rows.push(`<div><dt>${esc(L.reference)}</dt><dd dir="ltr" class="num">${esc(r.reference)}</dd></div>`);
    }
    el.setAttribute("aria-label", `${L[r.status] || L.unknown}: ${fmt(r.amount)} ${L.dinar} ${sub}`);
    el.innerHTML = `
      <div class="badge">${icon(ic)}</div>
      <h3>${title}</h3>
      <div class="r-amount">${num(r.amount)} <small>${cur}</small></div>
      ${sub ? `<p class="r-sub">${esc(sub)}</p>` : ""}
      <span class="r-money">${esc(money)}</span>
      ${rows.length ? `<dl class="rows">${rows.join("")}</dl>` : ""}
      ${r.status === "unknown" ? `<button type="button" class="btn" data-action="check">${esc(L.check)}</button>` : ""}`;
    return el;
  }

  function updateReceipt(r) {
    const old = $(`.receipt[data-qid="${CSS.escape(r.quote_id)}"]`, log);
    const fresh = renderReceipt(r);
    if (old) old.replaceWith(fresh); else append(fresh);
    closeCard(r.quote_id, ...receiptClose(r.status));
  }

  // ---------------------------------------------------------------- one server answer

  function handle(p, closeAs = CLOSE_AS.text) {
    const reply = p.reply || { lines: [], kind: "info" };
    if (reply.lang && LANG_UI[reply.lang]) setLang(reply.lang);
    for (const r of p.resolved || []) updateReceipt(r);
    const keepsCard = S.active && reply.kind === "card" && reply.quote_id === S.active.qid;
    if (S.active && !keepsCard) {
      const r = p.receipt && p.receipt.quote_id === S.active.qid ? p.receipt : null;
      closeCard(S.active.qid, ...(r ? receiptClose(r.status) : closeAs));
    }
    retireAnswers();
    for (const line of reply.lines || []) append(bubble("agent", line));
    if (p.receipt) append(renderReceipt(p.receipt));
    if (p.card && !(S.active && S.active.qid === p.card.quote_id)) {
      const el = append(renderCard(p.card), { force: true });
      armCard(el, p.card);
    }
    const checks = renderChecks(reply);
    if (checks) append(checks);
    const options = renderOptions(reply);
    if (options) append(options);
    if (p.voice_reply) playVoice(p.voice_reply);
    if (p.profile) setProfile(p.profile);
    if (p.receipt || (p.resolved && p.resolved.length)) refreshHistory();
  }

  function setLang(lang) {
    S.lang = lang;
    const input = $("#input");
    if (input) input.placeholder = U().placeholder[g()];
  }

  // A voice reply (VOICE_REPLY_MODE on the server): a small player in the thread, started at once.
  function playVoice(v) {
    if (!v || !v.data) return;
    const el = document.createElement("div");
    el.className = "msg agent voice";
    el.dir = "auto";
    const audio = document.createElement("audio");
    audio.controls = true;
    audio.preload = "auto";
    audio.src = `data:${v.mime || "audio/ogg"};base64,${v.data}`;
    audio.setAttribute("aria-label", U().voiceReply);
    el.appendChild(audio);
    append(el, { force: true });
    try { const p = audio.play(); if (p && p.catch) p.catch(() => { /* autoplay refused: the player is there */ }); } catch { /* no audio */ }
  }

  function setBusy(on) {
    S.busy = on;
    $$(".chip").forEach((c) => { c.disabled = on; });
    updateSend();
  }

  async function sendText(raw) {
    const text = (raw || "").trim();
    if (!text || S.busy) return;
    setBusy(true);
    append(bubble("user", text), { force: true });
    const typing = showTyping();
    try {
      const p = await api("/api/message", { method: "POST", body: { text } });
      typing.remove();
      handle(p, CLOSE_AS.text);
    } catch {
      typing.remove();
      append(bubble("error", T.netErr[g()]));
    } finally {
      setBusy(false);
    }
  }

  // A tapped option: only its id goes to the server, which looks it up against the open question.
  async function tapOption(id, title) {
    if (S.busy) return;
    setBusy(true);
    append(bubble("user", title), { force: true });
    const typing = showTyping();
    try {
      const p = await api("/api/option", { method: "POST", body: { option_id: id } });
      typing.remove();
      handle(p, CLOSE_AS.text);
    } catch {
      typing.remove();
      append(bubble("error", U().netErr[g()]));
    } finally {
      setBusy(false);
    }
  }

  async function tapButton(qid, action, label) {
    if (S.busy) return;
    setBusy(true);
    append(bubble("user", label), { force: true });
    const typing = showTyping();
    try {
      const p = await api("/api/button", { method: "POST", body: { quote_id: qid, button: action } });
      typing.remove();
      handle(p, CLOSE_AS[action] || CLOSE_AS.cancel);
    } catch {
      typing.remove();
      append(bubble("error", T.netErr[g()]));
    } finally {
      setBusy(false);
    }
  }

  async function checkNow() {
    if (S.busy) return;
    setBusy(true);
    const typing = showTyping();
    try {
      const p = await api("/api/check", { method: "POST" });
      typing.remove();
      handle(p);
    } catch {
      typing.remove();
      append(bubble("error", T.netErr[g()]));
    } finally {
      setBusy(false);
    }
  }

  log.addEventListener("click", (e) => {
    const o = e.target.closest("[data-option]");
    if (o) {
      if (!o.disabled) tapOption(o.dataset.option, ($("b", o) || o).textContent.trim());
      return;
    }
    const b = e.target.closest("[data-action]");
    if (!b || b.disabled) return;
    const action = b.dataset.action;
    if (action === "check") { checkNow(); return; }
    const card = b.closest(".card");
    if (!card || !S.active || S.active.qid !== card.dataset.qid) return;
    if (action === "confirm") openPin(card.dataset.qid, b);
    else tapButton(card.dataset.qid, action, b.textContent.trim());
  });

  // ---------------------------------------------------------------- sheets

  const app = $(".app");
  const project = (v, d = 0.998) => ((v / 1000) * d) / (1 - d); // momentum projection, px
  const rubber = (x, dim, c = 0.55) => (x * dim * c) / (dim + c * Math.abs(x));

  class Sheet {
    constructor(id, { onClose } = {}) {
      this.root = $(`#${id}`);
      this.panel = $(".sheet-panel", this.root);
      this.onClose = onClose;
      this.closing = false;
      this.root.addEventListener("click", (e) => { if (e.target.closest("[data-close]")) this.close(); });
      this.onKey = (e) => { if (e.key === "Escape") { e.preventDefault(); this.close(); } };
      this.drag();
    }
    get isOpen() { return !this.root.hidden && !this.closing; }
    open(trigger) {
      if (this.closing) { // grabbed again mid-close: reverse from where it is
        clearTimeout(this.closeTimer);
        this.closing = false;
        this.panel.style.transform = "";
        this.root.classList.add("open");
        return;
      }
      if (!this.root.hidden) return;
      this.trigger = trigger || document.activeElement;
      this.root.hidden = false;
      this.panel.style.transform = "";
      void this.panel.offsetHeight; // start the transition from the closed position
      this.root.classList.add("open");
      app.inert = true;
      document.addEventListener("keydown", this.onKey);
      const first = $("[data-autofocus]", this.panel) || $("button, [href], input", this.panel);
      if (first) first.focus({ preventScroll: true });
    }
    close() {
      if (this.root.hidden || this.closing) return;
      this.closing = true;
      const p = this.panel;
      if (p.style.transform && !wide.matches) p.style.transform = "translateY(100%)"; // keep going from the drag
      this.root.classList.remove("open");
      this.closeTimer = setTimeout(() => {
        this.root.hidden = true;
        this.closing = false;
        p.style.transform = "";
        p.style.transitionDuration = "";
        app.inert = false;
        document.removeEventListener("keydown", this.onKey);
        if (this.trigger && this.trigger.isConnected) this.trigger.focus({ preventScroll: true });
        if (this.onClose) this.onClose();
      }, reduceMotion.matches ? 220 : 430);
    }
    drag() {
      const head = $("[data-drag]", this.panel);
      if (!head) return;
      let start = null;
      let hist = [];
      head.addEventListener("pointerdown", (e) => {
        if (wide.matches || e.button !== 0 || e.target.closest("button")) return;
        start = { y: e.clientY, t: performance.now() };
        hist = [start];
        head.setPointerCapture(e.pointerId);
        this.panel.style.transition = "none";
      });
      head.addEventListener("pointermove", (e) => {
        if (!start) return;
        let dy = e.clientY - start.y;
        if (dy < 0) dy = -rubber(-dy, this.panel.offsetHeight); // soft edge upward
        this.panel.style.transform = `translateY(${dy}px)`;
        hist.push({ y: e.clientY, t: performance.now() });
        if (hist.length > 6) hist.shift();
      });
      const end = (e) => {
        if (!start) return;
        const dy = Math.max(0, e.clientY - start.y);
        const a = hist[0];
        const b = hist[hist.length - 1];
        const v = b.t > a.t ? ((b.y - a.y) / (b.t - a.t)) * 1000 : 0; // px/s, + is down
        start = null;
        this.panel.style.transition = "";
        const h = this.panel.offsetHeight;
        if (dy > 6 && dy + project(v) > h * 0.4) {
          if (v > 0) this.panel.style.transitionDuration = `${Math.max(0.16, Math.min(0.42, (h - dy) / v))}s`;
          this.close();
        } else {
          this.panel.style.transform = "translateY(0px)";
          setTimeout(() => { if (!this.closing) this.panel.style.transform = ""; }, 450);
        }
      };
      head.addEventListener("pointerup", end);
      head.addEventListener("pointercancel", end);
    }
  }

  const whoSheet = new Sheet("who-sheet");
  const historySheet = new Sheet("history-sheet");
  const pinSheet = new Sheet("pin-sheet", { onClose: () => { S.pin = null; } });

  $("#who-btn").addEventListener("click", (e) => {
    whoSheet.open(e.currentTarget);
    const current = $('.user-row[aria-checked="true"]');
    if (current) current.scrollIntoView({ block: "nearest" });
  });
  $("#history-btn").addEventListener("click", (e) => { historySheet.open(e.currentTarget); refreshHistory(); });

  // ---------------------------------------------------------------- PIN

  const EASTERN = { "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4", "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9",
    "۰": "0", "۱": "1", "۲": "2", "۳": "3", "۴": "4", "۵": "5", "۶": "6", "۷": "7", "۸": "8", "۹": "9" };
  const EASTERN_OF = "٠١٢٣٤٥٦٧٨٩";
  const pinDots = $("#pin-dots");
  const pinError = $("#pin-error");
  const pinBody = $(".pin-body");

  (function buildKeypad() {
    const keys = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "", "0", "del"];
    $("#keypad").innerHTML = keys.map((k) => {
      if (k === "") return '<span aria-hidden="true"></span>';
      if (k === "del") return `<button type="button" class="key ghost" data-key="del" aria-label="${U().delKey}">${icon("i-del")}</button>`;
      return `<button type="button" class="key" data-key="${k}" aria-label="${k}"><span>${k}<small>${EASTERN_OF[+k]}</small></span></button>`;
    }).join("");
  })();

  function renderDots() {
    const L = S.config.pin_length || 4;
    const n = S.pin ? S.pin.digits.length : 0;
    pinDots.innerHTML = Array.from({ length: L }, (_, i) => `<i class="${i < n ? "on" : ""}"></i>`).join("");
    pinDots.setAttribute("aria-label", U().dots(n, L));
  }

  function openPin(qid, trigger) {
    const c = S.active && S.active.data;
    if (!c) return;
    S.pin = { qid, digits: "", busy: false };
    $("#pin-title").textContent = U().pinTitle[g()];
    const del = $('#keypad [data-key="del"]');  // the keypad is built once; its label follows the language
    if (del) del.setAttribute("aria-label", U().delKey);
    $("#pin-summary").innerHTML = `${num(c.total)} ${esc((c.labels && c.labels.dinar) || "دينار")} · ${esc(c.payee)}`;
    $("#pin-hint").textContent = S.demoPin ? `${U().demoPin}: ${S.demoPin}` : "";
    const back = $("#pin-sheet [data-close]");
    if (back) back.textContent = U().back;
    pinError.textContent = "";
    pinBody.classList.remove("pin-busy");
    renderDots();
    pinSheet.open(trigger);
  }

  function pressKey(k) {
    const P = S.pin;
    if (!P || P.busy) return;
    const L = S.config.pin_length || 4;
    if (k === "del") P.digits = P.digits.slice(0, -1);
    else if (P.digits.length < L) P.digits += k;
    pinError.textContent = "";
    renderDots();
    if (P.digits.length === L) submitPin();
  }

  async function submitPin() {
    const P = S.pin;
    if (!P || P.busy) return;
    P.busy = true; // one tap, one request: a second tap waits for this answer
    pinBody.classList.add("pin-busy");
    pinError.innerHTML = `<span class="spinner" aria-hidden="true"></span>${esc(U().wait)}`;
    const qid = P.qid;
    try {
      const p = await api("/api/button", { method: "POST", body: { quote_id: qid, button: "confirm", pin: P.digits } });
      if (p.reply && p.reply.kind === "card" && p.reply.quote_id === qid) { // wrong PIN: the card stays open
        P.digits = "";
        P.busy = false;
        pinBody.classList.remove("pin-busy");
        renderDots();
        pinError.textContent = (p.reply.lines || []).join(" ");
        if (!reduceMotion.matches) { pinDots.classList.remove("shake"); void pinDots.offsetWidth; pinDots.classList.add("shake"); }
        vibrate([30, 40, 30]);
        if (p.profile) setProfile(p.profile);
        return;
      }
      pinSheet.close();
      const confirmLabel = ((S.active && S.active.data && S.active.data.buttons) || []).find((b) => b.id === "confirm");
      append(bubble("user", confirmLabel ? confirmLabel.label : U().yesPay), { force: true });
      handle(p, CLOSE_AS.cancel);
      if (p.receipt && p.receipt.status === "succeeded") vibrate(12);
      if (S.fault !== "biller_down" || (p.receipt && p.receipt.kind !== "transfer")) setFaultUi("none");
    } catch {
      P.digits = "";
      P.busy = false;
      pinBody.classList.remove("pin-busy");
      renderDots();
      pinError.textContent = T.netErr[g()];
    }
  }

  $("#keypad").addEventListener("click", (e) => {
    const k = e.target.closest("[data-key]");
    if (k) pressKey(k.dataset.key);
  });
  document.addEventListener("keydown", (e) => {
    if (!S.pin || $("#pin-sheet").hidden) return;
    const d = /^[0-9]$/.test(e.key) ? e.key : EASTERN[e.key];
    if (d) { e.preventDefault(); flashKey(d); pressKey(d); }
    else if (e.key === "Backspace") { e.preventDefault(); flashKey("del"); pressKey("del"); }
  });
  function flashKey(k) {
    const el = $(`.key[data-key="${k}"]`);
    if (!el) return;
    el.classList.add("pressed");
    setTimeout(() => el.classList.remove("pressed"), 110);
  }

  // ---------------------------------------------------------------- profile, chips, history, users

  function setProfile(p) {
    if (!p) return;
    S.profile = p;
    $("#who-name").textContent = p.name;
    $("#who-balance").innerHTML = `${num(p.balance)} دينار`;
    $("#avatar").textContent = (p.first_name || p.name || "؟").trim().charAt(0);
    $("#input").placeholder = U().placeholder[g()];
    for (const el of $$("[data-wallet]")) {
      el.innerHTML = `
        <div class="w-label">رصيد ${esc(p.first_name || p.name)}</div>
        <div class="w-balance">${num(p.balance)} <small>دينار</small></div>
        <div class="w-meta">
          ${p.daily_remaining !== null && p.daily_remaining !== undefined ? `<span>المتبقي من حد اليوم: ${num(p.daily_remaining)}</span>` : "<span></span>"}
          <span>الرقم ينتهي بـ <span class="num">${esc(p.phone_last4)}</span></span>
        </div>`;
    }
  }

  function renderChips(list) {
    const box = $("#chips");
    box.innerHTML = "";
    for (const s of list || []) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "chip";
      b.innerHTML = `<b>${esc(s.text)}</b>${s.hint ? `<small>${esc(s.hint)}</small>` : ""}`;
      b.setAttribute("aria-label", s.text);
      b.addEventListener("click", () => sendText(s.text));
      box.appendChild(b);
    }
  }

  function renderHistory(items) {
    const fresh = S.seen;
    S.seen = new Set(items.map((i) => i.quote_id));
    const html = items.length ? items.map((i) => {
      const [label, tone] = STATUS[i.status] || [i.status, ""];
      const isNew = fresh && !fresh.has(i.quote_id);
      return `<li class="h-item${isNew ? " fresh" : ""}">
        <span class="h-icon">${icon(KIND_ICON[i.kind] || "i-transfer")}</span>
        <span class="h-text"><span class="h-title">${esc(i.payee || KIND[i.kind] || "")}</span>
          <span class="h-sub">${esc([KIND[i.kind] || "", i.period || ""].join(" ").trim())}${i.created_at ? ` · ${esc(day(i.created_at))}` : ""}</span></span>
        <span class="h-amount"><b>${num(i.amount)}</b><span class="h-status ${tone}">${esc(label)}</span></span>
      </li>`;
    }).join("") : '<li class="history-empty">ماكو عمليات بعد.</li>';
    for (const ul of $$("[data-history]")) ul.innerHTML = html;
  }

  async function refreshHistory() {
    try {
      const h = await api("/api/history");
      renderHistory(h.items || []);
      if (h.profile) setProfile(h.profile);
    } catch { /* the chat still works */ }
  }

  function renderUsers() {
    const ul = $("#users");
    ul.setAttribute("role", "radiogroup");
    ul.setAttribute("aria-label", "المستخدمين التجريبيين");
    ul.innerHTML = S.users.map((u) => `
      <li><button type="button" class="user-row" role="radio" data-user="${esc(u.id)}" aria-checked="${S.profile && S.profile.id === u.id}">
        <span class="avatar" aria-hidden="true">${esc(u.name.charAt(0))}</span>
        <span class="u-text"><span class="u-name">${esc(u.name)}</span><span class="u-sub">${num(u.balance)} دينار</span></span>
        <span class="u-check">${icon("i-check")}</span>
      </button></li>`).join("");
  }
  $("#users").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-user]");
    if (!b) return;
    store.set("billpay.user", b.dataset.user);
    whoSheet.close();
    await startUser(b.dataset.user);
  });

  function renderFaults() {
    const box = $("#faults");
    box.insertAdjacentHTML("beforeend", FAULTS.map(([id, label, note]) => `
      <label class="fault"><input type="radio" name="fault" value="${id}" ${id === "none" ? "checked" : ""}>
        <span><b>${esc(label)}</b><small>${esc(note)}</small></span></label>`).join(""));
    box.addEventListener("change", async (e) => {
      const mode = e.target.value;
      try {
        await api("/api/demo-fault", { method: "POST", body: { mode } });
        S.fault = mode;
        toast(mode === "none" ? "رجعت الأمور طبيعية." : "تفعّلت للدفعة الجاية.");
      } catch { toast(T.netErr[g()]); setFaultUi(S.fault); }
    });
  }
  function setFaultUi(mode) {
    S.fault = mode;
    const r = $(`#faults input[value="${mode}"]`);
    if (r) r.checked = true;
  }

  let resetArmed = null;
  $("#reset-btn").addEventListener("click", async (e) => {
    const b = e.currentTarget;
    if (!resetArmed) {
      b.classList.add("armed");
      b.textContent = T.resetArm[g()];
      resetArmed = setTimeout(() => { resetArmed = null; b.classList.remove("armed"); b.textContent = "رجّع العرض من البداية"; }, 3500);
      return;
    }
    clearTimeout(resetArmed);
    resetArmed = null;
    b.classList.remove("armed");
    b.textContent = "رجّع العرض من البداية";
    try {
      const p = await api("/api/reset", { method: "POST" });
      whoSheet.close();
      applyStart(p);
      setFaultUi("none");
      toast("رجع العرض من البداية.");
    } catch { toast(T.netErr[g()]); }
  });

  let toastTimer = null;
  function toast(text) {
    const el = $("#toast");
    el.textContent = text;
    el.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("show"), 2400);
  }

  // ---------------------------------------------------------------- start a user

  function clearThread() {
    if (S.active) { clearInterval(S.active.timer); S.active = null; }
    log.innerHTML = "";
  }

  function applyStart(p) {
    clearThread();
    S.seen = null;
    setProfile(p.profile);
    S.demoPin = p.demo_pin || "";
    $("#welcome-title").textContent = `هلا ${p.profile ? p.profile.first_name : ""}`.trim();
    $("#welcome-body").textContent = T.welcome[g()];
    renderChips(p.suggestions);
    renderHistory(p.history || []);
    renderUsers();
    if (p.menu) { // the main menu, to tap: typing a request works just the same
      for (const line of p.menu.lines || []) log.appendChild(bubble("agent", line));
      const menu = renderOptions(p.menu);
      if (menu) log.appendChild(menu);
    }
    scroller.scrollTop = 0;
  }

  async function startUser(id) {
    try {
      applyStart(await api("/api/start", { method: "POST", body: { user_id: id } }));
    } catch {
      append(bubble("error", T.netErr[g()]));
    }
  }

  // ---------------------------------------------------------------- composer

  const input = $("#input");
  const send = $("#send");
  const form = $("#form");
  function updateSend() { send.disabled = S.busy || !input.value.trim(); }
  function grow() { input.style.height = "auto"; input.style.height = `${Math.min(input.scrollHeight, 140)}px`; }
  input.addEventListener("input", () => { grow(); updateSend(); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); form.requestSubmit(); }
  });
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    if (S.busy) return;
    const text = input.value;
    if (!text.trim()) return;
    input.value = "";
    grow();
    updateSend();
    sendText(text);
  });

  const composer = $("#composer");
  if ("ResizeObserver" in window) {
    new ResizeObserver(() => {
      document.documentElement.style.setProperty("--composer-h", `${composer.offsetHeight}px`);
    }).observe(composer);
  }

  // ---------------------------------------------------------------- voice notes

  const mic = $("#mic");
  const recStatus = $("#rec-status");
  function setRecording(on) {
    form.classList.toggle("recording", on);
    mic.setAttribute("aria-pressed", String(on));
    mic.setAttribute("aria-label", on ? "وقّف التسجيل وارسل" : "رسالة صوتية");
  }

  async function sendVoice(blob) {
    if (S.busy) return;
    setBusy(true);
    const typing = showTyping();
    try {
      const p = await api("/api/voice", { method: "POST", raw: blob, type: blob.type || "audio/webm" });
      typing.remove();
      if (p.transcript) append(bubble("user", p.transcript, { voice: true }), { force: true });
      if (p.reply) handle(p, CLOSE_AS.text);
      else if (p.note) append(bubble("note", p.note));
    } catch {
      typing.remove();
      append(bubble("error", T.netErr[g()]));
    } finally {
      setBusy(false);
    }
  }

  mic.addEventListener("click", async () => {
    if (S.rec) { S.rec.stop(); return; }
    if (!S.config.voice) { // the server answers with a friendly note when the loaded core has no voice
      try {
        const p = await api("/api/voice", { method: "POST", raw: new Blob([]), type: "audio/webm" });
        if (p && p.note) append(bubble("note", p.note), { force: true });
      } catch { append(bubble("error", T.netErr[g()])); }
      return;
    }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder) { toast(T.micDenied[g()]); return; }
    let stream;
    try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); } catch { toast(T.micDenied[g()]); return; }
    const type = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg"].find((t) => MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(t)) || "";
    const rec = new MediaRecorder(stream, type ? { mimeType: type } : undefined);
    const chunks = [];
    const started = Date.now();
    rec.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
    rec.onstop = () => {
      stream.getTracks().forEach((t) => t.stop());
      clearInterval(rec.clock);
      S.rec = null;
      setRecording(false);
      const blob = new Blob(chunks, { type: rec.mimeType || type || "audio/webm" });
      if (blob.size > 800 && Date.now() - started > 500) sendVoice(blob);
    };
    rec.clock = setInterval(() => {
      const s = Math.floor((Date.now() - started) / 1000);
      recStatus.innerHTML = `يسجّل… <span class="num">${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}</span>`;
      if (s >= 60) rec.stop();
    }, 250);
    recStatus.textContent = "يسجّل…";
    rec.start();
    S.rec = rec;
    setRecording(true);
  });

  // ---------------------------------------------------------------- go

  async function init() {
    renderFaults();
    renderDots();
    try {
      const [config, users] = await Promise.all([api("/api/config"), api("/api/users")]);
      S.config = { ...S.config, ...config };
      S.users = users;
    } catch {
      append(bubble("error", "ما گدرنا نوصل للخادم. حدّث الصفحة."));
      return;
    }
    const saved = store.get("billpay.user");
    const pick = S.users.find((u) => u.id === saved) || S.users.find((u) => u.id === "u02") || S.users[0];
    if (pick) await startUser(pick.id);
  }
  init();
})();
