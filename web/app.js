// Fish 音色库页面逻辑（原生 JS，无框架依赖）
"use strict";

const API = "/api/plugin/kira_plugin_fishaudio";

// ---------- 通用请求封装 ----------

async function request(path, options = {}) {
  const response = await fetch(API + path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    let message = "请求失败（" + response.status + "）";
    try {
      const data = await response.json();
      if (data && data.detail) message = data.detail;
    } catch (_) { /* 忽略解析失败，用默认文案 */ }
    if (response.status === 401) message = "登录已失效，请重新登录 Kira。";
    throw new Error(message);
  }
  return response.json();
}

// ---------- 状态提示 ----------

function showStatus(text, isError = false) {
  const el = document.getElementById("status");
  if (!text) { el.hidden = true; return; }
  el.textContent = text;
  el.style.background = isError ? "#fef1f1" : "#fff7e6";
  el.style.borderColor = isError ? "#f3c0c2" : "#ffd591";
  el.hidden = false;
}

// ---------- 音色列表渲染 ----------

function renderVoices(data) {
  const rows = document.getElementById("voice-rows");
  const empty = document.getElementById("empty-tip");
  const foot = document.getElementById("table-foot");

  rows.innerHTML = "";
  for (const v of data.voices) {
    const tr = document.createElement("tr");
    tr.dataset.voice = v.name;

    const num = document.createElement("td");
    num.textContent = String(v.idx);

    const name = document.createElement("td");
    name.textContent = v.name;
    if (v.name === data.active) {
      const badge = document.createElement("span");
      badge.className = "badge";
      badge.textContent = "默认";
      name.appendChild(badge);
    }

    const idCell = document.createElement("td");
    idCell.className = "id-cell";
    idCell.textContent = v.id;

    const note = document.createElement("td");
    note.textContent = v.note || "—";

    const ops = document.createElement("td");
    ops.appendChild(actionButton("试听", () => testWith(v.id)));
    if (v.name !== data.active) {
      ops.appendChild(actionButton("设为默认", () => setDefault(v.name)));
    }
    ops.appendChild(actionButton("删除", () => removeWithConfirm(v.name), true));

    tr.append(num, name, idCell, note, ops);
    rows.appendChild(tr);
  }

  empty.hidden = data.voices.length > 0;
  foot.textContent = `共 ${data.count} 个音色（上限 ${data.max_voices}）${data.active ? "，当前默认：" + data.active : "，还没有默认音色"}${data.config_default ? "，配置兜底：" + data.config_default : ""}`;
  renderTestVoiceSelect(data);
}

function actionButton(label, onClick, danger = false) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "btn" + (danger ? " btn-danger" : "");
  btn.textContent = label;
  btn.addEventListener("click", onClick);
  return btn;
}

// ---------- 试听音色下拉 ----------

function renderTestVoiceSelect(data) {
  const select = document.getElementById("test-voice");
  const prev = select.value;
  select.innerHTML = "";
  const options = [
    { value: "", label: data.active ? "默认音色：" + data.active : "默认音色" },
    ...data.voices.map((v) => ({ value: v.id, label: v.name + "（" + v.id + "）" })),
  ];
  for (const opt of options) {
    const el = document.createElement("option");
    el.value = opt.value;
    el.textContent = opt.label;
    select.appendChild(el);
  }
  // 尽量保留上次选择
  if (prev && [...select.options].some((o) => o.value === prev)) select.value = prev;
}

// ---------- 数据操作 ----------

async function refresh() {
  try {
    const data = await request("/voices");
    renderVoices(data);
    showStatus("");
  } catch (e) {
    showStatus(e.message, true);
  }
}

async function addVoice(event) {
  event.preventDefault();
  const name = document.getElementById("add-name").value.trim();
  const id = document.getElementById("add-id").value.trim();
  const note = document.getElementById("add-note").value.trim();
  try {
    await request("/voices/save", {
      method: "POST",
      body: JSON.stringify({ name, id, note }),
    });
    document.getElementById("add-form").reset();
    await refresh();
    showStatus("已保存音色「" + name + "」。");
  } catch (e) {
    showStatus(e.message, true);
  }
}

async function setDefault(name) {
  try {
    await request("/voices/use", {
      method: "POST",
      body: JSON.stringify({ key: name }),
    });
    await refresh();
    showStatus("已把「" + name + "」设为默认音色。");
  } catch (e) {
    showStatus(e.message, true);
  }
}

// 删除需要等 3 秒确认，防止误删
function removeWithConfirm(name) {
  const row = document.querySelector(`tr[data-voice="${CSS.escape(name)}"]`);
  const btn = row ? row.querySelector(".btn-danger") : null;
  if (btn && btn.dataset.confirming !== "1") {
    btn.dataset.confirming = "1";
    const original = btn.textContent;
    btn.textContent = "再点一次确认删除";
    btn.disabled = true;
    setTimeout(() => {
      btn.textContent = original;
      btn.disabled = false;
      delete btn.dataset.confirming;
    }, 3000);
    return;
  }
  doRemove(name);
}

async function doRemove(name) {
  try {
    await request("/voices/remove", {
      method: "POST",
      body: JSON.stringify({ key: name }),
    });
    await refresh();
    showStatus("已删除音色「" + name + "」。");
  } catch (e) {
    showStatus(e.message, true);
  }
}

// ---------- 试听 ----------

function testWith(referenceId) {
  document.getElementById("test-voice").value = referenceId || "";
  runTest();
}

async function runTest() {
  const btn = document.getElementById("btn-test");
  const text = document.getElementById("test-text").value.trim();
  const referenceId = document.getElementById("test-voice").value;
  btn.disabled = true;
  try {
    const data = await request("/tts/test", {
      method: "POST",
      body: JSON.stringify({ text, reference_id: referenceId }),
    });
    const audio = document.getElementById("test-audio");
    audio.src = "data:" + data.mime + ";base64," + data.audio;
    document.getElementById("test-result").hidden = false;
    document.getElementById("test-meta").textContent =
      "长度 " + data.length + " 字节" + (referenceId ? "，音色：" + referenceId : "，默认音色");
    audio.play().catch(() => { /* 浏览器可能拦截自动播放，交给用户点击 */ });
    showStatus("");
  } catch (e) {
    showStatus(e.message, true);
  } finally {
    btn.disabled = false;
  }
}

// ---------- 事件绑定与初始化 ----------

document.getElementById("btn-refresh").addEventListener("click", refresh);
document.getElementById("add-form").addEventListener("submit", addVoice);
document.getElementById("btn-test").addEventListener("click", runTest);

refresh();
