"use strict";

// 階段文字標籤；視覺用 .stage-badge::before 的色點處理。
const STAGE_LABEL = {
  empty: "空集",
  needs_transcribe: "未轉字幕",
  needs_assemble: "未合成",
  done: "完成",
  broken: "損毀",
};

let isOpening = false;

async function withButton(btn, fn) {
  if (btn.disabled) return;
  btn.disabled = true;
  try {
    await fn();
  } finally {
    btn.disabled = false;
  }
}

function renderLoadError(message) {
  const loading = document.getElementById("loading");
  loading.classList.add("error");
  loading.innerHTML = "";

  const head = document.createElement("div");
  head.innerHTML = `<span data-icon="alert-triangle" data-icon-size="18"></span>`;
  const msg = document.createElement("div");
  msg.textContent = "載入失敗：" + message;
  const retry = document.createElement("button");
  retry.type = "button";
  retry.className = "retry-btn";
  retry.textContent = "重試";
  retry.addEventListener("click", () => {
    loading.classList.remove("error");
    loadEpisodes();
  });

  loading.appendChild(head);
  loading.appendChild(msg);
  loading.appendChild(retry);
  loading.hidden = false;
  if (window.Icons) window.Icons.inject(loading);
}

async function loadEpisodes() {
  const loading = document.getElementById("loading");
  const empty = document.getElementById("empty");
  const list = document.getElementById("episode-list");
  const warningsBox = document.getElementById("warnings");

  loading.classList.remove("error");
  loading.innerHTML = `<span class="spinner" aria-hidden="true"></span><span>載入集數中…</span>`;
  loading.hidden = false;
  empty.hidden = true;
  list.hidden = true;
  warningsBox.hidden = true;

  try {
    const r = await fetch("/api/episodes");
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();

    loading.hidden = true;

    if ((data.warnings || []).length) {
      warningsBox.textContent = (data.warnings || []).join(" / ");
      warningsBox.hidden = false;
    }

    if (data.episodes.length === 0) {
      empty.hidden = false;
      if (window.Icons) window.Icons.inject(empty);
      return;
    }

    list.innerHTML = "";
    for (const ep of data.episodes) {
      const li = document.createElement("li");
      li.className = "episode-card";
      li.innerHTML = `
        <div class="ep-head">
          <div>
            <h3 class="ep-name"></h3>
            <div class="ep-date"></div>
          </div>
          <span class="stage-badge stage-${ep.stage}"></span>
        </div>
      `;
      li.querySelector(".ep-name").textContent = ep.name;
      li.querySelector(".ep-date").textContent = ep.date || "—";
      li.querySelector(".stage-badge").textContent =
        STAGE_LABEL[ep.stage] || ep.stage;
      li.addEventListener("click", () => {
        if (isOpening) return;
        openEpisode(ep.path, li);
      });
      list.appendChild(li);
    }
    list.hidden = false;
  } catch (err) {
    renderLoadError(err.message);
  }
}

async function openEpisode(path, cardEl) {
  if (isOpening) return;
  isOpening = true;
  if (cardEl) cardEl.classList.add("opening");
  try {
    const r = await fetch("/api/episodes/open", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    if (!r.ok) {
      const detail = await r.json().catch(() => ({ detail: r.statusText }));
      alert("開啟失敗：" + detail.detail);
      loadEpisodes();
      return;
    }
    window.location.href = "/";
  } finally {
    isOpening = false;
    if (cardEl) cardEl.classList.remove("opening");
  }
}

// 設定視窗「選資料夾…」：跳系統原生選資料夾 → 直接填進「集數存放資料夾」欄位，免打字。
async function pickRootFolder() {
  const btn = document.getElementById("roots-pick-btn");
  const input = document.getElementById("roots-input");
  await withButton(btn, async () => {
    const r = await fetch("/api/episode/pick", { method: "POST" });
    const data = await r.json();
    if (data.cancelled || !data.path) return;
    input.value = data.path;
  });
}

async function pickFolder() {
  const btn = document.getElementById("open-folder-btn");
  await withButton(btn, async () => {
    const r = await fetch("/api/episode/pick", { method: "POST" });
    const data = await r.json();
    if (data.cancelled || !data.path) return;

    const preview = await fetch("/api/episode/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: data.path }),
    }).then((r) => r.json());

    if (preview.has_episode_yaml) {
      await openEpisode(data.path);
      return;
    }

    if (
      !confirm(
        `「${preview.folder_name}」還沒初始化。要跑 init 嗎？\n會建立：${preview.subdirs_to_create.join("、")}`,
      )
    ) {
      return;
    }
    const initR = await fetch("/api/episode/init", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: data.path }),
    });
    if (!initR.ok) {
      const d = await initR.json().catch(() => ({}));
      alert("init 失敗：" + (d.detail || initR.statusText));
      return;
    }
    await openEpisode(data.path);
  });
}

const ASSET_LABEL = {
  intro: "intro",
  outro_audio: "outro 音樂",
  outro_image: "outro 卡片",
  logo: "浮水印 logo（選用）",
};

function renderStatusPill(label, ok, hintTitle) {
  const pill = document.createElement("span");
  pill.className = `status-pill status-pill-${ok ? "ok" : "missing"}`;
  if (hintTitle) pill.title = hintTitle;
  pill.innerHTML = `<span class="status-dot" aria-hidden="true"></span><span class="status-label"></span><span class="status-mark">${ok ? "✓" : "✗"}</span>`;
  pill.querySelector(".status-label").textContent = label;
  return pill;
}

function renderConfigStatus(cfg) {
  const sttBox = document.getElementById("config-status-stt");
  const assetsBox = document.getElementById("config-status-assets");
  if (!sttBox || !assetsBox) return;
  sttBox.innerHTML = "";
  assetsBox.innerHTML = "";

  // 本地 Breeze 引擎狀態（/api/config 的 breeze 欄位；免金鑰，只看有沒有安裝）
  const breeze = cfg.breeze || {};
  sttBox.appendChild(
    renderStatusPill(
      "本地 Breeze",
      !!breeze.available,
      breeze.available
        ? breeze.dir || ""
        : "尚未安裝：請在 toolkit 目錄執行 ./install.sh",
    ),
  );

  const assets = cfg.assets || {};
  for (const key of ["intro", "outro_audio", "outro_image", "logo"]) {
    const info = assets[key];
    if (!info) continue;
    assetsBox.appendChild(
      renderStatusPill(ASSET_LABEL[key], info.exists, info.path),
    );
  }
}

function openSettingsModal() {
  const modal = document.getElementById("settings-modal");
  const input = document.getElementById("roots-input");
  fetch("/api/config")
    .then((r) => r.json())
    .then((cfg) => {
      input.value = (cfg.episode_roots || [])[0] || "";
      renderConfigStatus(cfg);
      modal.showModal();
    });
}

async function saveSettings() {
  const btn = document.getElementById("settings-save");
  await withButton(btn, async () => {
    const input = document.getElementById("roots-input");
    const path = input.value.trim();
    const roots = path ? [path] : [];
    const r = await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ episode_roots: roots }),
    });
    if (!r.ok) {
      alert("儲存失敗");
      return;
    }
    document.getElementById("settings-modal").close();
    loadEpisodes();
  });
}

function openNewEpisodeModal() {
  const today = new Date();
  const yyyymmdd =
    today.getFullYear().toString() +
    String(today.getMonth() + 1).padStart(2, "0") +
    String(today.getDate()).padStart(2, "0");
  document.getElementById("new-date").value = yyyymmdd;
  document.getElementById("new-name").value = "";
  document.getElementById("new-ep-error").hidden = true;
  document.getElementById("new-episode-modal").showModal();
}

async function createNewEpisode() {
  const btn = document.getElementById("new-ep-create");
  await withButton(btn, async () => {
    const date = document.getElementById("new-date").value.trim();
    const name = document.getElementById("new-name").value.trim();
    const errBox = document.getElementById("new-ep-error");
    errBox.hidden = true;

    const r = await fetch("/api/episode/new", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ date, name }),
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      errBox.textContent = d.detail || r.statusText;
      errBox.hidden = false;
      return;
    }
    window.location.href = "/";
  });
}

document
  .getElementById("open-folder-btn")
  .addEventListener("click", pickFolder);
document
  .getElementById("new-episode-btn")
  .addEventListener("click", openNewEpisodeModal);
document
  .getElementById("settings-btn")
  .addEventListener("click", openSettingsModal);
document
  .getElementById("settings-save")
  .addEventListener("click", saveSettings);
document
  .getElementById("roots-pick-btn")
  .addEventListener("click", pickRootFolder);
document
  .getElementById("new-ep-create")
  .addEventListener("click", createNewEpisode);

for (const id of ["new-date", "new-name"]) {
  document.getElementById(id).addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      createNewEpisode();
    }
  });
}
document.getElementById("roots-input").addEventListener("keydown", (e) => {
  // 單行路徑欄位：Enter 直接儲存（含 Cmd/Ctrl+Enter）。
  if (e.key === "Enter") {
    e.preventDefault();
    saveSettings();
  }
});

// 兩組關閉按鈕（右上 X + 底部「取消」）共用 close()
function bindClose(modalId, ...btnIds) {
  const modal = document.getElementById(modalId);
  for (const bid of btnIds) {
    const btn = document.getElementById(bid);
    if (btn) btn.addEventListener("click", () => modal.close());
  }
}
bindClose("settings-modal", "settings-cancel", "settings-cancel-2");
bindClose("new-episode-modal", "new-ep-cancel", "new-ep-cancel-2");

// 初始 icon 注入
if (window.Icons) window.Icons.inject();

loadEpisodes();
