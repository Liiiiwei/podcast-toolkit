"use strict";

const STAGE_LABEL = {
  empty: "⬜ 空集",
  needs_transcribe: "⚪ 未轉字幕",
  needs_assemble: "🟡 未合成",
  done: "🟢 完成",
  broken: "⚠ 損毀",
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
  loading.textContent = "";
  const msg = document.createElement("div");
  msg.textContent = "⚠ 載入失敗：" + message;
  const retry = document.createElement("button");
  retry.type = "button";
  retry.className = "retry-btn";
  retry.textContent = "重試";
  retry.addEventListener("click", () => {
    loading.classList.remove("error");
    loadEpisodes();
  });
  loading.appendChild(msg);
  loading.appendChild(retry);
  loading.hidden = false;
}

async function loadEpisodes() {
  const loading = document.getElementById("loading");
  const empty = document.getElementById("empty");
  const list = document.getElementById("episode-list");
  const warningsBox = document.getElementById("warnings");

  loading.classList.remove("error");
  loading.textContent = "載入中…";
  loading.hidden = false;
  empty.hidden = true;
  list.hidden = true;
  warningsBox.hidden = true;

  try {
    const r = await fetch("/api/episodes");
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();

    loading.hidden = true;

    if (data.warnings.length) {
      warningsBox.textContent = data.warnings.join(" / ");
      warningsBox.hidden = false;
    }

    if (data.episodes.length === 0) {
      empty.hidden = false;
      return;
    }

    list.innerHTML = "";
    for (const ep of data.episodes) {
      const li = document.createElement("li");
      li.className = "episode-card";
      li.innerHTML = `
        <div>
          <h3 class="ep-name"></h3>
          <div class="ep-date"></div>
        </div>
        <span class="stage-badge stage-${ep.stage}"></span>
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

async function pickFolder() {
  const btn = document.getElementById("open-folder-btn");
  await withButton(btn, async () => {
    const r = await fetch("/api/episode/pick", { method: "POST" });
    const data = await r.json();
    if (data.cancelled || !data.path) return;

    // 開的可能是 episode（有 yaml）或要 init 的資料夾
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

function openSettingsModal() {
  const modal = document.getElementById("settings-modal");
  const input = document.getElementById("roots-input");
  fetch("/api/config")
    .then((r) => r.json())
    .then((cfg) => {
      input.value = (cfg.episode_roots || []).join("\n");
      modal.showModal();
    });
}

async function saveSettings() {
  const btn = document.getElementById("settings-save");
  await withButton(btn, async () => {
    const input = document.getElementById("roots-input");
    const roots = input.value
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean);
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
    // new_episode 已切了 holder["ep"]，直接導向 edit UI
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
  .getElementById("new-ep-create")
  .addEventListener("click", createNewEpisode);

// 按 Enter 在 modal 輸入框內 → 觸發主要動作（避免落入 form 預設 submit）
for (const id of ["new-date", "new-name"]) {
  document.getElementById(id).addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      createNewEpisode();
    }
  });
}
document.getElementById("roots-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
    e.preventDefault();
    saveSettings();
  }
});

// 取消鈕：脫離 <form method="dialog"> 後，要手動 close()
document
  .getElementById("settings-cancel")
  .addEventListener("click", () =>
    document.getElementById("settings-modal").close(),
  );
document
  .getElementById("new-ep-cancel")
  .addEventListener("click", () =>
    document.getElementById("new-episode-modal").close(),
  );

loadEpisodes();
