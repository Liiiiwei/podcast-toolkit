/* 共用 toast 系統（index.html 與 dashboard.html 掛同一套 —— 「幾個地方在管」原則，只此一份）。
 * 取代 alert() 當回饋 UI：只換提示形式、不阻斷操作；呼叫端原本的中止流程（return 等）自己保留。
 *
 * 介面：showToast(message, kind)，kind ∈ "error" | "warn"
 *   - warn（操作攔阻／輸入驗證）：約 4 秒自動消失
 *   - error（錯誤回報）：約 8 秒；兩者皆可點擊立即關閉
 *
 * top-layer 對策：兩頁的 modal 都是原生 <dialog>.showModal()（進 top-layer），
 * 一般 position:fixed 的 toast 會被開著的 modal 與其 backdrop 蓋住 —— 而 alert 的
 * 大宗呼叫點正是 modal 內（詞庫、cam、mic-setup…）。所以容器用 Popover API
 * （popover="manual"）也進 top-layer，且每次顯示 toast 都 re-show 一次，
 * 確保疊在「最晚開啟的 dialog」之上（top-layer 按開啟順序疊放）。
 * 不支援 Popover 的環境退回 CSS fixed 定位（modal 外仍可見）。 */
(function () {
  "use strict";

  var DURATION_MS = { warn: 4000, error: 8000 };

  var container = null;

  function ensureContainer() {
    if (container && document.body.contains(container)) return container;
    container = document.createElement("div");
    container.id = "toast-container";
    if ("showPopover" in container) {
      container.setAttribute("popover", "manual");
    }
    document.body.appendChild(container);
    return container;
  }

  // top-layer 是按開啟順序疊的：後開的 dialog 蓋住先開的 popover。
  // 每次有新 toast 都 hide→show 一次，把容器重新提到 top-layer 最上。
  function raiseToTopLayer(el) {
    if (!("showPopover" in el) || !el.hasAttribute("popover")) return;
    try {
      el.hidePopover();
    } catch (_) {
      /* 尚未 show 過會拋，無妨 */
    }
    try {
      el.showPopover();
    } catch (_) {
      /* 已 show 或未連上 DOM，無妨 */
    }
  }

  function dismiss(toast) {
    if (toast._dismissed) return;
    toast._dismissed = true;
    clearTimeout(toast._timer);
    toast.classList.add("toast-leave");
    toast.addEventListener(
      "transitionend",
      function () {
        toast.remove();
      },
      { once: true },
    );
    // transition 沒觸發（prefers-reduced-motion 等）時的保險移除
    setTimeout(function () {
      toast.remove();
    }, 400);
  }

  function showToast(message, kind) {
    var k = kind === "warn" ? "warn" : "error";
    var box = ensureContainer();
    var toast = document.createElement("div");
    toast.className = "toast toast-" + k;
    // 無障礙：error 用 assertive（role=alert）、warn 用 polite（role=status）
    toast.setAttribute("role", k === "error" ? "alert" : "status");
    toast.textContent = String(message);
    toast.title = "點擊關閉";
    toast.addEventListener("click", function () {
      dismiss(toast);
    });
    box.appendChild(toast);
    raiseToTopLayer(box);
    // 進場動畫：先套 enter（位移＋透明），下兩幀移除讓 transition 跑
    toast.classList.add("toast-enter");
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        toast.classList.remove("toast-enter");
      });
    });
    toast._timer = setTimeout(function () {
      dismiss(toast);
    }, DURATION_MS[k]);
    return toast;
  }

  window.showToast = showToast;
})();
