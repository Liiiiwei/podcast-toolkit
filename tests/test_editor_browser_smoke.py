"""編輯器瀏覽器煙霧測試：真 Chrome（CDP）+ 真後端，斷言行為而非原始碼長相。

**這支是 Phase 3「拆 app.js」的驗收門檻。**
既有的 `test_editor_ui_smoke.py` 只做靜態字串斷言（`'"#xxx"' in APP_JS`），
當不了重構的護欄 —— 它會因為「程式碼搬到別的檔案」而誤紅，卻在「模組整個載入失敗」時是綠的。
這支反過來：只看瀏覽器裡實際發生的事，所以程式碼怎麼搬都不會誤紅，
而只要 `app.js` 拆壞（module 載不起來、事件沒綁、存檔鏈斷掉）就一定紅。

驗四件事（每一項都綁布林斷言，不只印值 —— scripts/cdp-walkthrough/README.md 斷言紀律）：
  S1 載入時零個未捕捉的 JS 例外
  S2 字幕卡渲染出來（4 張，對應 conftest 的 SAMPLE_SRT）
  S3 時間軸畫出區塊 + 播放頭
  S4 改字 → 存檔 → 真的落到磁碟上的 `_final_v2.srt`

環境需求：`websockets` 套件 + 一份 Chrome。缺任一就整支 skip
（本機 `/usr/bin/python3` 3.9.6 兩者都有；Homebrew 的 python3 缺 audioop 跑不動後端）。
"""
from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

import pytest
import yaml

# 跟 conftest.tmp_episode_dir 共用同一份素材（不另抄一份，避免兩邊漂移）
from tests.conftest import SAMPLE_SRT

try:
    import websockets  # noqa: F401
except ImportError:  # pragma: no cover - 環境沒裝就整支跳過
    websockets = None

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
]

# 改字用的標記：必須不在原始 SRT 裡，否則「有沒有存進去」根本分不出來
MARK = "煙霧測試改過的字"


def _find_chrome() -> str | None:
    for p in CHROME_CANDIDATES:
        if Path(p).exists():
            return p
    return None


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _CDP:
    """一條 WebSocket 上跑 browser + page 兩個 session。

    id 用單一計數器（不是每個 session 各自數）—— 兩個 session 各自從 1 開始會撞號，
    回應被派給錯的等待者，症狀是隨機 timeout。
    """

    def __init__(self, ws):
        self.ws = ws
        self._id = 0
        self._pending: dict = {}
        self.exceptions: list = []
        self.console: list = []
        self.loaded = False

    async def send(self, method, params=None, session_id=None):
        self._id += 1
        mid = self._id
        msg = {"id": mid, "method": method, "params": params or {}}
        if session_id:
            msg["sessionId"] = session_id
        fut = asyncio.get_running_loop().create_future()
        self._pending[mid] = fut
        await self.ws.send(json.dumps(msg))
        return await asyncio.wait_for(fut, timeout=60)

    async def pump(self):
        async for raw in self.ws:
            data = json.loads(raw)
            mid = data.get("id")
            if mid is not None and mid in self._pending:
                fut = self._pending.pop(mid)
                if not fut.done():
                    if "error" in data:
                        fut.set_exception(RuntimeError(str(data["error"])))
                    else:
                        fut.set_result(data.get("result", {}))
                continue
            method = data.get("method")
            if method == "Page.loadEventFired":
                self.loaded = True
            elif method == "Runtime.exceptionThrown":
                det = (data.get("params") or {}).get("exceptionDetails") or {}
                desc = (det.get("exception") or {}).get("description") or det.get("text")
                self.exceptions.append(desc or json.dumps(det)[:200])
            elif method == "Runtime.consoleAPICalled":
                if (data.get("params") or {}).get("type") == "error":
                    args = (data.get("params") or {}).get("args") or []
                    self.console.append(
                        " ".join(str(a.get("value") or a.get("description") or "") for a in args)
                    )


def _make_episode(root: Path) -> Path:
    """最小 episode 資料夾（結構對齊 conftest.tmp_episode_dir）。"""
    ep = root / "20260601 測試集"
    ep.mkdir()
    for sub in ("01_母帶", "03_成品", "04_工作檔"):
        (ep / sub).mkdir()
    (ep / "episode.yaml").write_text(
        yaml.safe_dump(
            {
                "date": 20260601,
                "name": "測試集",
                "main_video": "01_母帶/{name}.mp4",
                "main_srt": "01_母帶/{name}.srt",
                "fixes": [],
                "card_fixes": [],
                "force_break": [],
                "force_join": [],
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (ep / "03_成品" / "測試集_final_v2.srt").write_text(SAMPLE_SRT, encoding="utf-8")
    # 假的正片，讓 /api/video 有東西可讀（內容不是真影片，<video> 解不開是預期的，
    # 那只會產生 media error，不會變成未捕捉的 JS 例外，所以不影響 S1）
    (ep / "01_母帶" / "測試集.mp4").write_bytes(b"FAKE" * 1000)
    return ep


async def _drive(base_url: str, ws_url: str, srt_path: Path) -> dict:
    facts: dict = {}
    async with websockets.connect(ws_url, max_size=None, ping_interval=None) as ws:
        cdp = _CDP(ws)
        pump = asyncio.ensure_future(cdp.pump())
        try:
            t = await cdp.send("Target.createTarget", {"url": "about:blank"})
            a = await cdp.send(
                "Target.attachToTarget", {"targetId": t["targetId"], "flatten": True}
            )
            sid = a["sessionId"]
            await cdp.send("Page.enable", session_id=sid)
            await cdp.send("Runtime.enable", session_id=sid)
            await cdp.send("Network.setCacheDisabled", {"cacheDisabled": True}, session_id=sid)

            async def js(expr):
                r = await cdp.send(
                    "Runtime.evaluate",
                    {"expression": expr, "returnByValue": True, "awaitPromise": True},
                    session_id=sid,
                )
                if "exceptionDetails" in r:
                    return {"__exc": str(r["exceptionDetails"])[:400]}
                return (r.get("result") or {}).get("value")

            cdp.exceptions.clear()
            cdp.loaded = False
            await cdp.send("Page.navigate", {"url": base_url + "/"}, session_id=sid)
            for _ in range(150):
                if cdp.loaded:
                    break
                await asyncio.sleep(0.1)

            # 等 renderCards 跑完（輪詢而不是 sleep 死等：慢的時候不會假紅、快的時候不空等）
            for _ in range(80):
                n = await js("document.querySelectorAll('#cards-list .card').length")
                if isinstance(n, int) and n > 0:
                    break
                await asyncio.sleep(0.25)
            await asyncio.sleep(1.0)  # 讓時間軸／波形這類後續非同步渲染收斂

            facts["load_exceptions"] = list(cdp.exceptions)
            facts["cards"] = await js("document.querySelectorAll('#cards-list .card').length")
            facts["first_card_text"] = await js(
                "(document.querySelector('#cards-list .card .card-text')||{}).textContent"
            )
            facts["tl_blocks"] = await js(
                "document.querySelectorAll('#card-timeline .tl-block').length"
            )
            facts["tl_playhead"] = await js("!!document.querySelector('#tl-playhead')")
            facts["tl_total"] = await js(
                "parseFloat((document.querySelector('#card-timeline')||{dataset:{}})"
                ".dataset.total||'0')"
            )

            # ---- S4 存檔 round-trip ----
            # 卡片文字是在 **blur** 收的（app.js:2411），不是 input；
            # 而且新字必須跟原字不同，否則 app.js:2436 會提早 return 什麼都不做。
            #
            # 髒值訊號是 `#unsaved-badge` 的 hidden class（app.js:466-473），
            # **不是** `#save-btn.disabled` —— 存檔鈕只在「尚未轉字幕」與「全刪」時才 disabled
            # （app.js:445／464），它從來不反映有沒有未存變更。
            facts["dirty_before"] = await js(
                "!document.querySelector('#unsaved-badge').classList.contains('hidden')"
            )
            facts["edit_applied"] = await js(
                "(() => {"
                "  const el = document.querySelector('#cards-list .card .card-text');"
                "  if (!el) return false;"
                "  el.focus();"
                f"  el.textContent = {json.dumps(MARK)};"
                "  el.blur();"
                "  return true;"
                "})()"
            )
            await asyncio.sleep(0.5)
            facts["dirty_after_edit"] = await js(
                "!document.querySelector('#unsaved-badge').classList.contains('hidden')"
            )
            facts["unsaved_count"] = await js(
                "document.querySelector('#unsaved-count').textContent"
            )
            await js("document.querySelector('#save-btn').click()")

            saved = False
            deadline = time.time() + 20
            while time.time() < deadline:
                if MARK in srt_path.read_text(encoding="utf-8"):
                    saved = True
                    break
                await asyncio.sleep(0.25)
            facts["saved_to_disk"] = saved
            facts["console_errors"] = list(cdp.console)
            facts["exceptions_after_save"] = list(cdp.exceptions)
        finally:
            pump.cancel()
    return facts


@pytest.fixture(scope="module")
def smoke(tmp_path_factory):
    """開真後端 + 真 Chrome 跑一次走查，回傳事實 dict；各 test 只對它做斷言。

    一次啟動跑完所有互動（而不是每個 test 各開一次 Chrome）：省 20 秒，
    而且四個斷言拆成四個 test，早期失敗不會遮蔽後面的結果。
    """
    if websockets is None:
        pytest.skip("沒有 websockets 套件，跳過瀏覽器煙霧測試")
    chrome = _find_chrome()
    if not chrome:
        pytest.skip("找不到 Chrome，跳過瀏覽器煙霧測試")

    import uvicorn

    from podcast_toolkit.episode import Episode
    from podcast_toolkit.web import api
    from podcast_toolkit.web.api import build_app

    root = tmp_path_factory.mktemp("browser-smoke")
    ep_dir = _make_episode(root)
    srt_path = ep_dir / "03_成品" / "測試集_final_v2.srt"

    # conftest 的 _isolate_user_config 是 function-scoped，比本 fixture 晚生效 ——
    # 這裡跑的是真伺服器，不自己導開的話會寫進使用者真正的 ~/.podcast-toolkit/config.json。
    # （late-bound：路由呼叫當下才查 api 模組 global，所以 build_app 之前 setattr 就有效）
    orig_cfg, orig_typo = api.CONFIG_PATH, api.TYPO_DICT_PATH
    api.CONFIG_PATH = root / "_cfg.json"
    api.TYPO_DICT_PATH = root / "_typo.json"

    # _idle_threshold_sec 預設 90 秒會自己關掉；走查期間不能讓它收工
    app = build_app(Episode(ep_dir), shutdown=lambda: None, _idle_threshold_sec=1e9)

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if getattr(server, "started", False):
            break
        time.sleep(0.05)

    cdp_port = _free_port()
    profile = tmp_path_factory.mktemp("chrome-profile")  # 全新 profile：舊 profile 的媒體快取壞掉會讓載入永遠不完成
    proc = subprocess.Popen(
        [
            chrome,
            "--headless=new",
            f"--remote-debugging-port={cdp_port}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-gpu",
            "--mute-audio",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    ws_url = None
    for _ in range(120):
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{cdp_port}/json/version", timeout=1
            ) as r:
                ws_url = json.loads(r.read())["webSocketDebuggerUrl"]
            break
        except Exception:
            time.sleep(0.25)

    try:
        if not ws_url:
            pytest.skip("Chrome 沒有在時限內開出 CDP 埠，跳過瀏覽器煙霧測試")
        yield asyncio.run(_drive(f"http://127.0.0.1:{port}", ws_url, srt_path))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            proc.kill()
        server.should_exit = True
        thread.join(timeout=10)
        api.CONFIG_PATH, api.TYPO_DICT_PATH = orig_cfg, orig_typo


def test_s1_no_uncaught_js_exception_on_load(smoke):
    """app.js 整包載得起來 —— 拆檔拆壞（少 export／路徑錯）第一個死在這。"""
    assert smoke["load_exceptions"] == [], f"載入時有未捕捉例外：{smoke['load_exceptions']}"


def test_s2_cards_rendered(smoke):
    """字幕卡真的渲染出來，且文字是 SRT 裡的內容（不是空殼）。"""
    assert smoke["cards"] == 4, f"字幕卡張數不對：{smoke['cards']}"
    assert smoke["first_card_text"] == "大家好歡迎來到我愛上班", (
        f"第一張卡文字不對：{smoke['first_card_text']!r}"
    )


def test_s3_timeline_rendered(smoke):
    """時間軸畫出區塊 + 播放頭，且總長是從卡片時間算出來的（不是 0）。"""
    assert smoke["tl_blocks"] == 4, f"時間軸區塊數不對：{smoke['tl_blocks']}"
    assert smoke["tl_playhead"] is True, "時間軸沒有播放頭"
    assert smoke["tl_total"] and smoke["tl_total"] > 0, (
        f"時間軸總長不合理：{smoke['tl_total']}"
    )


def test_s4_edit_and_save_round_trip(smoke):
    """改字 → 未存徽章亮 → 按存檔 → 新字真的落到磁碟上的 _final_v2.srt。

    只驗「按鈕能按」不算數（前科：寫得進讀不回）；證據是磁碟上的檔案。
    """
    assert smoke["edit_applied"] is True, "找不到可編輯的卡片文字"
    assert smoke["dirty_before"] is False, "還沒改東西未存徽章就亮了（髒值判斷失效）"
    assert smoke["dirty_after_edit"] is True, "改完字未存徽章沒亮"
    assert smoke["unsaved_count"] == "1", f"未存筆數不對：{smoke['unsaved_count']!r}"
    assert smoke["saved_to_disk"] is True, (
        f"存檔沒有寫進 _final_v2.srt；console 錯誤：{smoke['console_errors']}"
        f"／例外：{smoke['exceptions_after_save']}"
    )
