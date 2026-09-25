"""pytest fixtures：建臨時 episode 資料夾；外加編輯器前端模組清單（跨測試檔共用）。"""
from pathlib import Path

import pytest
import yaml

# ── 編輯器前端模組清單（app.js 是進入點，其餘是 Phase 3 之後陸續抽出的）─────
# 為什麼放這裡：不只一個測試檔要對「串接後的全文」做靜態斷言（目前是
# test_editor_ui_smoke.py 與 test_cam_modal_save_payload.py）。清單各留一份的話，
# 下次再抽一個模組就會有某個檔忘了更新 —— 「程式碼只是搬家卻被判成被刪」正是這樣
# 發生的（api.js 抽出當下就踩到一次）。一份清單，一個地方管。
EDITOR_JS = [
    "app.js",
    "render.js",
    "timeline.js",
    "api.js",
    "timeline-core.js",
    "shortcuts.js",
]


def editor_static_dir() -> Path:
    """前端靜態檔目錄。走套件而不是相對路徑，跟既有測試檔的取法一致。"""
    import podcast_toolkit.web as web_pkg

    return Path(web_pkg.__file__).parent / "static"


def editor_js_source() -> str:
    """所有編輯器前端模組的串接原始碼，app.js 在最前（依 EDITOR_JS 順序）。"""
    static = editor_static_dir()
    return "\n".join((static / name).read_text(encoding="utf-8") for name in EDITOR_JS)


@pytest.fixture(autouse=True)
def _isolate_user_config(tmp_path, monkeypatch):
    """把 ~/.podcast-toolkit/config.json / typo-dict.json 導到隔離 tmp。

    否則測試（例如 /api/episode/new、/api/episodes/open 會呼叫 add_recent）會寫進
    使用者真實的 config，污染 recent_episodes 清單、把真集擠掉。late-bound lambda
    在路由呼叫當下才查 api 模組 global，所以在 build_app 之前 setattr 就能生效。
    """
    from podcast_toolkit.web import api
    monkeypatch.setattr(api, "CONFIG_PATH", tmp_path / "_pcfg_config.json")
    monkeypatch.setattr(api, "TYPO_DICT_PATH", tmp_path / "_pcfg_typo.json")


SAMPLE_SRT = """\
1
00:00:00,000 --> 00:00:04,200
大家好歡迎來到我愛上班

2
00:00:04,200 --> 00:00:12,000
今天要聊的是過嗨乳牛這個議題

3
00:00:12,000 --> 00:00:14,000
呃那個

4
00:00:14,000 --> 00:00:22,000
我們從牠的飼料配方開始講起
"""


@pytest.fixture
def tmp_episode_dir(tmp_path: Path) -> Path:
    """建一個最小 episode 資料夾結構，回傳路徑。"""
    ep = tmp_path / "20260601 測試集"
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

    # 放一個 v2 srt 在 03_成品/
    (ep / "03_成品" / "測試集_final_v2.srt").write_text(SAMPLE_SRT, encoding="utf-8")

    return ep


@pytest.fixture
def tmp_episode_with_crops(tmp_episode_dir: Path) -> Path:
    """在 tmp_episode_dir 之上補 crop_yt + crop_reels 到 yaml。"""
    yaml_path = tmp_episode_dir / "episode.yaml"
    yaml_path.write_text(
        yaml_path.read_text(encoding="utf-8")
        + "crop_yt:\n  x: 0.1\n  y: 0.0\n  width: 0.8\n  height: 1.0\n"
        + "crop_reels:\n  x: 0.3\n  y: 0.0\n  width: 0.4\n  height: 1.0\n",
        encoding="utf-8",
    )
    return tmp_episode_dir


@pytest.fixture
def tmp_episode_full(tmp_episode_dir: Path, monkeypatch) -> Path:
    """在 tmp_episode_dir 之上補齊 prepare_assembly 需要的檔案：
    - 01_母帶/{name}.mp4 stub（空檔，由 monkeypatch ffprobe_duration 蓋掉量測）
    - assemble.ffprobe_duration 回傳固定 100.0 秒
    - shutil.which 對 ffmpeg / ffprobe 都回傳 True（避免本機沒裝測試直接掛掉）
    - speed.enabled=false：預設倍速已改成開啟 1.1x，會把所有時間軸斷言（main_dur、
      字幕時間、雙行 margin）乘上 1/1.1。這裡刻意關掉當基準線，測倍速的測試自己覆寫。
    """
    # 母帶 stub
    (tmp_episode_dir / "01_母帶" / "測試集.mp4").write_bytes(b"")

    ep_yaml = tmp_episode_dir / "episode.yaml"
    _data = yaml.safe_load(ep_yaml.read_text(encoding="utf-8"))
    _data["speed"] = {"enabled": False}
    # 多數既有 filter 字串測試固定驗證 legacy select 路徑；seek 路徑由專屬測試覆蓋。
    _data["encode"] = {"seek_cut_inputs": False}
    ep_yaml.write_text(
        yaml.safe_dump(_data, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )

    from podcast_toolkit import assemble as _asm

    monkeypatch.setattr(_asm, "ffprobe_duration", lambda _p: 100.0)
    monkeypatch.setattr(_asm.shutil, "which", lambda _name: "/usr/bin/" + _name)

    return tmp_episode_dir


@pytest.fixture
def tmp_episode_full_multicam(tmp_episode_full: Path) -> Path:
    """在 tmp_episode_full 之上補齊雙鏡頭資產：
    - 01_母帶/測試集_camB.mp4 stub
    - episode.yaml 補 cameras + camera_sync_offset
    - 03_成品/測試集_final_v2.cameras.json sidecar（卡 3 標 b → 其餘 carry-forward a）
    """
    (tmp_episode_full / "01_母帶" / "測試集_camB.mp4").write_bytes(b"")

    yaml_path = tmp_episode_full / "episode.yaml"
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    data["cameras"] = {
        "a": "01_母帶/{name}.mp4",
        "b": "01_母帶/{name}_camB.mp4",
    }
    data["camera_sync_offset"] = {"b": 1.25}
    yaml_path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    # sidecar：卡 3 切到 b（_v2.srt 已在 tmp_episode_dir 寫入）
    sidecar = tmp_episode_full / "03_成品" / "測試集_final_v2.cameras.json"
    sidecar.write_text('{"3": "b"}', encoding="utf-8")

    return tmp_episode_full
