"""剪輯指令（原型匯出的 plan JSON）套進一集：解析、寫回、round-trip、API 端點。"""
import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from podcast_toolkit import config, srt_io
from podcast_toolkit.episode import Episode
from podcast_toolkit.web import plan_io
from podcast_toolkit.web.api import build_app


def _plan(**over):
    """一份最小但合法的剪輯指令，欄位比照 video-edit-prototype.js 的 buildPlan()。"""
    base = {
        "version": 1,
        "source": "real",
        "duration": 24.0,
        "finalDuration": 22.0,
        "cuts": [[4.0, 6.0]],
        "keep": [[0.0, 4.0], [6.0, 24.0]],
        "subtitles": [
            {"start": 0.0, "end": 4.2, "text": "大家好歡迎來到我愛上班",
             "outStart": 0.0, "outEnd": 4.0, "dropped": False},
            {"start": 4.2, "end": 12.0, "text": "今天要聊的是過嗨乳牛這個議題",
             "outStart": 4.0, "outEnd": 10.0, "dropped": False},
        ],
        "cards": [{"start": 1.0, "end": 3.0, "tpl": "big", "text": "標題卡",
                   "scale": 1.0, "x": 0.5, "y": 0.5}],
        "style": {"font_size": 72, "margin_v": 120, "primary_colour_hex": "#ffcc00",
                  "outline_colour_hex": "#000000", "alignment": 2},
    }
    base.update(over)
    return base


# ── 解析與正規化 ────────────────────────────────────────────────

def test_parse_plan_accepts_json_string_and_dict():
    a = plan_io.parse_plan(_plan())
    b = plan_io.parse_plan(json.dumps(_plan()))
    assert a == b
    assert a["cuts"] == [[4.0, 6.0]]
    assert len(a["subtitles"]) == 2
    assert len(a["cards"]) == 1 and a["cards"][0]["tpl"] == "big"


def test_ass_colour_is_bgr_not_rgb():
    """ASS 是 &HAABBGGRR，跟 HTML #rrggbb 的順序相反 —— 寫反了字幕會變色。"""
    s = plan_io.normalize_style({"primary_colour_hex": "#ffcc00"})
    assert s["primary_colour"] == "&H0000CCFF"


def test_normalize_style_only_emits_given_keys():
    """只寫使用者調過的鍵，其餘留給 defaults.yaml —— 這是深合併能 round-trip 的前提。"""
    s = plan_io.normalize_style({"font_size": 60})
    assert s == {"font_size": 60}
    assert plan_io.normalize_style(None) == {}


def test_cuts_accept_both_shapes_and_get_sorted():
    p = plan_io.parse_plan(_plan(cuts=[{"start": 9.0, "end": 10.0}, [2.0, 3.0]]))
    assert p["cuts"] == [[2.0, 3.0], [9.0, 10.0]]


def test_empty_subtitles_are_skipped_and_counted():
    subs = _plan()["subtitles"] + [{"start": 13.0, "end": 14.0, "text": "   "}]
    p = plan_io.parse_plan(_plan(subtitles=subs))
    assert len(p["subtitles"]) == 2 and p["empty_subtitles"] == 1


@pytest.mark.parametrize("bad, hint", [
    ({"version": 9}, "版本不合"),
    ({"cuts": "x"}, "cuts"),
    ({"subtitles": None}, "subtitles"),
    ({"cuts": [[5.0, 5.0]]}, "迄點不大於起點"),
    ({"style": {"primary_colour_hex": "紅色"}}, "色碼"),
])
def test_bad_plan_raises_planerror(bad, hint):
    with pytest.raises(plan_io.PlanError) as e:
        plan_io.parse_plan(_plan(**bad))
    assert hint in str(e.value)


def test_not_json_at_all_raises():
    with pytest.raises(plan_io.PlanError):
        plan_io.parse_plan("{壞掉的")
    with pytest.raises(plan_io.PlanError):
        plan_io.parse_plan([1, 2, 3])


# ── 寫回一集 ────────────────────────────────────────────────────

def test_apply_plan_round_trip(tmp_episode_dir: Path):
    """寫入 → 重載 Episode → 讀回原值。設定類改動只有 round-trip 才算驗過。"""
    ep = Episode(tmp_episode_dir)
    plan = plan_io.parse_plan(_plan())
    summary = plan_io.apply_plan(ep, plan)

    ep2 = Episode(tmp_episode_dir)  # 重載，走完整 config.merge
    assert ep2.cfg["cuts"] == [[4.0, 6.0]]
    st = ep2.cfg["subtitle_style"]
    assert st["font_size"] == 72 and st["margin_v"] == 120
    assert st["primary_colour"] == "&H0000CCFF"
    # 沒送的鍵仍吃 defaults（深合併沒被整段換掉）
    assert st["font_name"] == config.load_defaults()["subtitle_style"]["font_name"]

    cards = srt_io.parse(ep2.output_v2_srt().read_text(encoding="utf-8"))
    assert [c["text"] for c in cards] == [s["text"] for s in plan["subtitles"]]
    assert [c["start"] for c in cards] == [0.0, 4.2]  # 原始時間軸，不是剪後時間
    assert summary["cards"] == 1
    assert summary["cut_total_sec"] == 2.0

    # 標題卡真的落到 episode.yaml，重載後讀得回來（config.py 是 deny-list，不需白名單）
    tc = ep2.cfg["title_cards"]
    assert [(c["start"], c["end"], c["tpl"], c["text"]) for c in tc] == [
        (1.0, 3.0, "big", "標題卡")
    ]


def test_apply_plan_backs_up_old_srt(tmp_episode_dir: Path):
    ep = Episode(tmp_episode_dir)
    old = ep.output_v2_srt().read_text(encoding="utf-8")
    plan_io.apply_plan(ep, plan_io.parse_plan(_plan()))
    bak = ep.output_v2_srt().with_suffix(".srt.bak")
    assert bak.read_text(encoding="utf-8") == old


def test_empty_cuts_clears_the_key(tmp_episode_dir: Path):
    """沒有剪除就要把 cuts 拿掉，不是留 [] —— 否則舊的剪除會一直留在 yaml。"""
    ep = Episode(tmp_episode_dir)
    plan_io.apply_plan(ep, plan_io.parse_plan(_plan()))
    plan_io.apply_plan(Episode(tmp_episode_dir), plan_io.parse_plan(_plan(cuts=[])))
    data = yaml.safe_load((tmp_episode_dir / "episode.yaml").read_text(encoding="utf-8"))
    assert "cuts" not in data


def test_apply_plan_keeps_untouched_yaml_keys(tmp_episode_dir: Path):
    """yaml 裡沒被指令碰到的欄位（fixes、手寫的 subtitle_style 鍵）不能被洗掉。"""
    yaml_path = tmp_episode_dir / "episode.yaml"
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    data["subtitle_style"] = {"shadow": 3}
    yaml_path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                         encoding="utf-8")
    plan_io.apply_plan(Episode(tmp_episode_dir), plan_io.parse_plan(_plan()))
    after = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert after["name"] == "測試集" and "fixes" in after
    assert after["subtitle_style"]["shadow"] == 3      # 手寫的留著
    assert after["subtitle_style"]["font_size"] == 72  # 指令的疊上去


# ── API 端點 ────────────────────────────────────────────────────

@pytest.fixture
def client(tmp_episode_dir: Path):
    (tmp_episode_dir / "01_母帶" / "測試集.mp4").write_bytes(b"FAKE" * 100)
    return TestClient(build_app(Episode(tmp_episode_dir), shutdown=lambda: None))


def test_api_apply_plan_ok(client, tmp_episode_dir: Path):
    r = client.post("/api/apply-plan", json={"plan": _plan()})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["assemble"] is None
    assert body["applied"]["subtitles"] == 2 and body["applied"]["cards"] == 1
    assert body["applied"]["srt_backup"].endswith(".srt.bak")
    assert Episode(tmp_episode_dir).cfg["cuts"] == [[4.0, 6.0]]


def test_api_accepts_bare_plan_as_payload(client):
    assert client.post("/api/apply-plan", json=_plan()).status_code == 200


def test_api_bad_plan_is_400_and_changes_nothing(client, tmp_episode_dir: Path):
    before = (tmp_episode_dir / "episode.yaml").read_text(encoding="utf-8")
    srt = Episode(tmp_episode_dir).output_v2_srt()
    srt_before = srt.read_text(encoding="utf-8")
    r = client.post("/api/apply-plan", json={"plan": _plan(version=9)})
    assert r.status_code == 400 and "版本不合" in r.json()["detail"]
    assert (tmp_episode_dir / "episode.yaml").read_text(encoding="utf-8") == before
    assert srt.read_text(encoding="utf-8") == srt_before  # 半套的 SRT 比明講失敗更難發現


def test_api_refuses_to_wipe_all_subtitles(client):
    r = client.post("/api/apply-plan", json={"plan": _plan(subtitles=[])})
    assert r.status_code == 400 and "清空" in r.json()["detail"]


def test_api_409_while_assembling(client, monkeypatch):
    from podcast_toolkit.web import assemble_job
    monkeypatch.setattr(assemble_job, "is_busy", lambda: True)
    r = client.post("/api/apply-plan", json={"plan": _plan()})
    assert r.status_code == 409 and "合成" in r.json()["detail"]


def test_api_starts_assemble_when_targets_given(client, monkeypatch, tmp_episode_dir: Path):
    """帶 targets 才開合成，且拿到的必須是「重新 init 過、吃得到新 cuts」的 Episode。"""
    seen = {}
    from podcast_toolkit.web.routes import editor as editor_mod

    def fake_start(ep, **kw):
        seen["cuts"] = ep.cfg["cuts"]
        seen["kw"] = kw
        return {"targets": kw["targets"], "out_paths": ["03_成品/測試集_yt.mp4"]}

    monkeypatch.setattr(editor_mod.assemble_job, "start_job", fake_start)
    r = client.post("/api/apply-plan",
                    json={"plan": _plan(), "targets": ["yt"], "preview_sec": 30})
    assert r.status_code == 200, r.text
    assert r.json()["assemble"]["targets"] == ["yt"]
    assert seen["cuts"] == [[4.0, 6.0]]        # 不是舊的 Episode
    assert seen["kw"]["preview_sec"] == 30


def test_api_assemble_error_is_400(client, monkeypatch):
    """AssembleError 繼承 RuntimeError，攔錯順序會讓它變成 409。"""
    from podcast_toolkit.assemble import AssembleError
    from podcast_toolkit.web.routes import editor as editor_mod

    def boom(ep, **kw):
        raise AssembleError("找不到母帶")

    monkeypatch.setattr(editor_mod.assemble_job, "start_job", boom)
    r = client.post("/api/apply-plan", json={"plan": _plan(), "targets": ["yt"]})
    assert r.status_code == 400 and "找不到母帶" in r.json()["detail"]
