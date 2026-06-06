"""assemble.py 的 filter_complex 字串組裝測試。"""
import pytest
import yaml

from podcast_toolkit import assemble
from podcast_toolkit.assemble import prepare_assembly


BASE_CFG = {
    "encode": {
        "resolution": "1920x1080",
        "framerate": 30,
        "pix_fmt": "yuv420p",
        "audio_sample_rate": 48000,
    },
    "assets": {
        "intro_duration": 5,
        "intro_fade_out": 1,
        "outro_duration": 5,
    },
    "subtitle_style": {
        "font_name": "F", "font_size": 28, "bold": 1,
        "primary_colour": "&H00FFFFFF", "outline_colour": "&H00000000",
        "border_style": 1, "outline": 2, "shadow": 0, "margin_v": 60,
    },
    "crop_yt": None,
    "crop_reels": None,
    "deletions": [],
}


def test_filter_complex_yt_no_crop_no_deletions(monkeypatch):
    fc = assemble.build_filter_complex_yt(BASE_CFG, main_dur=100.0, srt_rel="x.srt")
    assert "crop=" not in fc
    assert "select=" not in fc


def test_filter_complex_yt_with_crop_adds_crop_filter():
    cfg = {**BASE_CFG, "crop_yt": {"x": 0.1, "y": 0.05, "width": 0.8, "height": 0.9}}
    fc = assemble.build_filter_complex_yt(cfg, main_dur=100.0, srt_rel="x.srt")
    # 1920 * 0.8 = 1536, 1080 * 0.9 = 972, x=192, y=54
    assert "crop=1536:972:192:54" in fc


def test_filter_complex_yt_with_crop_rescales_back_to_resolution():
    """crop 後必須 scale 回 1920x1080，否則 concat 會因尺寸不符失敗。"""
    cfg = {**BASE_CFG, "crop_yt": {"x": 0.1, "y": 0.05, "width": 0.8, "height": 0.9}}
    fc = assemble.build_filter_complex_yt(cfg, main_dur=100.0, srt_rel="x.srt")
    # crop 後緊接著 scale 回原解析度
    assert "crop=1536:972:192:54,scale=1920:1080" in fc


def test_filter_complex_yt_with_deletions_adds_select():
    cfg = {**BASE_CFG, "deletions": [3]}
    intervals = [(12.0, 14.0)]
    fc = assemble.build_filter_complex_yt(
        cfg, main_dur=100.0, srt_rel="x.srt", deletion_intervals=intervals
    )
    assert "select='not(between(t" in fc
    assert "between(t,12.000,14.000)" in fc.replace(" ", "")
    assert "aselect=" in fc


def test_build_deletion_intervals_returns_card_time_ranges(tmp_episode_dir):
    from podcast_toolkit import assemble as asm
    intervals = asm.build_deletion_intervals(
        v2_srt_path=tmp_episode_dir / "03_成品" / "測試集_final_v2.srt",
        deletions=[3],
    )
    assert intervals == [(12.0, 14.0)]


def test_filter_deletion_srt_writes_clean_srt(tmp_path):
    from podcast_toolkit import assemble as asm
    src = tmp_path / "in.srt"
    src.write_text(
        "1\n00:00:00,000 --> 00:00:04,000\nA\n\n"
        "2\n00:00:04,000 --> 00:00:08,000\nB\n\n"
        "3\n00:00:08,000 --> 00:00:12,000\nC\n",
        encoding="utf-8",
    )
    out = tmp_path / "out.srt"
    asm.filter_deletion_srt(src, out, deletions=[2])
    text = out.read_text(encoding="utf-8")
    assert "B" not in text
    assert "A" in text and "C" in text


# --- prepare_assembly：YT / Reels 分支 ---

def test_prepare_assembly_yt_uses_yt_output(tmp_episode_full):
    """output_kind='yt' 時輸出檔是 _YT完整版.mp4。"""
    plan = prepare_assembly(tmp_episode_full, output_kind="yt", force=True)
    assert plan["out"].name.endswith("_YT完整版.mp4")
    # cmd 包含 intro 和 outro
    assert any("intro" in str(a) for a in plan["cmd"])


def test_prepare_assembly_tmp_out_keeps_mp4_extension(tmp_episode_full):
    """tmp_out 必須以 .mp4 結尾，否則 ffmpeg 無法從副檔名判斷輸出格式。"""
    plan = prepare_assembly(tmp_episode_full, output_kind="yt", force=True)
    tmp_out = plan["tmp_out"]
    # .X.mp4.tmp 會讓 ffmpeg 報 "Unable to choose an output format"
    assert tmp_out.suffix == ".mp4", f"tmp_out 副檔名要是 .mp4，目前是 {tmp_out.suffix}"
    # 仍是隱藏檔（以 . 開頭），方便清理
    assert tmp_out.name.startswith(".")


def test_prepare_assembly_reels_skips_intro_outro(tmp_episode_full):
    """output_kind='reels' 時 ffmpeg cmd 不含 intro / outro 輸入。"""
    plan = prepare_assembly(tmp_episode_full, output_kind="reels", force=True)
    assert plan["out"].name.endswith("_Reels.mp4")
    # Reels cmd 只有 1 個 -i（main video），不含 intro/outro
    i_count = sum(1 for a in plan["cmd"] if a == "-i")
    assert i_count == 1
    # filter_complex 應該沒有 concat
    fc_idx = plan["cmd"].index("-filter_complex")
    assert "concat" not in plan["cmd"][fc_idx + 1]


def test_prepare_assembly_reels_uses_crop_reels(tmp_episode_full):
    """Reels 分支讀 cfg['crop_reels'] 而非 crop_yt。"""
    ep_yaml = tmp_episode_full / "episode.yaml"
    data = yaml.safe_load(ep_yaml.read_text(encoding="utf-8"))
    data["crop_yt"] = {"x": 0.1, "y": 0.0, "width": 0.8, "height": 1.0}
    data["crop_reels"] = {"x": 0.3, "y": 0.0, "width": 0.4, "height": 1.0}
    ep_yaml.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    plan = prepare_assembly(tmp_episode_full, output_kind="reels", force=True)
    fc_idx = plan["cmd"].index("-filter_complex")
    fc = plan["cmd"][fc_idx + 1]
    # Reels 解析度 1080x1920，crop_reels width=0.4 → 432px
    assert "crop=432:1920:324:0" in fc


def test_prepare_assembly_reels_resolution_1080x1920(tmp_episode_full):
    plan = prepare_assembly(tmp_episode_full, output_kind="reels", force=True)
    fc_idx = plan["cmd"].index("-filter_complex")
    fc = plan["cmd"][fc_idx + 1]
    assert "scale=1080:1920" in fc


def test_prepare_assembly_yt_head_trim_appends_deletion_at_start(tmp_episode_full):
    """T21: cfg['head_trim_sec'] > 0 時，ffmpeg filter 要把 [0, head_trim] 當刪除區間。"""
    ep_yaml = tmp_episode_full / "episode.yaml"
    data = yaml.safe_load(ep_yaml.read_text(encoding="utf-8"))
    data["head_trim_sec"] = 1.5
    ep_yaml.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    plan = prepare_assembly(tmp_episode_full, output_kind="yt", force=True)
    fc_idx = plan["cmd"].index("-filter_complex")
    fc = plan["cmd"][fc_idx + 1].replace(" ", "")
    assert "between(t,0.000,1.500)" in fc


def test_prepare_assembly_yt_tail_trim_appends_deletion_at_end(tmp_episode_full):
    """T21: cfg['tail_trim_sec'] > 0 時，filter 要把 [total - tail, total] 當刪除區間。
    tmp_episode_full 的 ffprobe_duration mock 回 100.0，所以 tail=2 → 區間 (98, 100)。
    """
    ep_yaml = tmp_episode_full / "episode.yaml"
    data = yaml.safe_load(ep_yaml.read_text(encoding="utf-8"))
    data["tail_trim_sec"] = 2.0
    ep_yaml.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    plan = prepare_assembly(tmp_episode_full, output_kind="yt", force=True)
    fc_idx = plan["cmd"].index("-filter_complex")
    fc = plan["cmd"][fc_idx + 1].replace(" ", "")
    assert "between(t,98.000,100.000)" in fc


def test_prepare_assembly_yt_trim_reduces_main_dur_for_fade_out(tmp_episode_full):
    """T21: head + tail trim 應該扣 main_dur，否則 fade-out 時間點算錯。
    head=1.5 + tail=2 → main_dur 由 100 變成 96.5。
    """
    ep_yaml = tmp_episode_full / "episode.yaml"
    data = yaml.safe_load(ep_yaml.read_text(encoding="utf-8"))
    data["head_trim_sec"] = 1.5
    data["tail_trim_sec"] = 2.0
    ep_yaml.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    plan = prepare_assembly(tmp_episode_full, output_kind="yt", force=True)
    assert plan["main_dur"] == pytest.approx(96.5)


def test_prepare_assembly_reels_head_trim_also_applies(tmp_episode_full):
    """T21: Reels 分支也要套頭尾 trim。"""
    ep_yaml = tmp_episode_full / "episode.yaml"
    data = yaml.safe_load(ep_yaml.read_text(encoding="utf-8"))
    data["head_trim_sec"] = 0.8
    ep_yaml.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    plan = prepare_assembly(tmp_episode_full, output_kind="reels", force=True)
    fc_idx = plan["cmd"].index("-filter_complex")
    fc = plan["cmd"][fc_idx + 1].replace(" ", "")
    assert "between(t,0.000,0.800)" in fc


def test_prepare_assembly_reels_crop_rescales_back_to_1080x1920(tmp_episode_full):
    """Reels crop 後必須 scale 回 1080×1920，否則輸出會變 432×1920 之類的怪尺寸。"""
    ep_yaml = tmp_episode_full / "episode.yaml"
    data = yaml.safe_load(ep_yaml.read_text(encoding="utf-8"))
    data["crop_reels"] = {"x": 0.3, "y": 0.0, "width": 0.4, "height": 1.0}
    ep_yaml.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    plan = prepare_assembly(tmp_episode_full, output_kind="reels", force=True)
    fc_idx = plan["cmd"].index("-filter_complex")
    fc = plan["cmd"][fc_idx + 1]
    # crop=432:1920:324:0 後緊接 scale=1080:1920，標準 IG/TikTok 規格
    assert "crop=432:1920:324:0,scale=1080:1920" in fc


# --- T23a Step 4b: 雙鏡頭 multicam filter_complex ---

ONE_SEG_A = [{"cam": "a", "start": 0.0, "end": 50.0}]
TWO_SEG_AB = [
    {"cam": "a", "start": 0.0, "end": 20.0},
    {"cam": "b", "start": 20.0, "end": 50.0},
]


def test_filter_complex_yt_multicam_single_segment_no_concat():
    """單段（整集都 cam a）→ trim + setpts，但不需 segment concat。"""
    fc = assemble.build_filter_complex_yt_multicam(
        BASE_CFG, main_dur=50.0, srt_rel="x.srt", segments=ONE_SEG_A,
    )
    assert "trim=0.000:50.000" in fc
    assert "[seg_v_0]" in fc
    assert "[seg_a_0]" in fc
    # 單段不需 segment-level concat（只剩最後 intro+main+outro 的 concat=n=3）
    assert "concat=n=3:v=1:a=1[v][a]" in fc


def test_filter_complex_yt_multicam_two_segments_concat():
    """兩段 a→b → 兩個 trim + segment concat=n=2。"""
    fc = assemble.build_filter_complex_yt_multicam(
        BASE_CFG, main_dur=50.0, srt_rel="x.srt", segments=TWO_SEG_AB,
    )
    assert "trim=0.000:20.000" in fc
    assert "trim=20.000:50.000" in fc
    # segment concat：兩段視訊+音訊 → concat=n=2
    assert "concat=n=2:v=1:a=1[main_v_raw][main_a_raw]" in fc
    # 最後仍要和 intro/outro concat=n=3
    assert "concat=n=3:v=1:a=1[v][a]" in fc


def test_filter_complex_yt_multicam_cam_b_uses_b_input():
    """cam b 段要從 [m_b_v] trim，不是 [m_a_v]。"""
    fc = assemble.build_filter_complex_yt_multicam(
        BASE_CFG, main_dur=50.0, srt_rel="x.srt", segments=TWO_SEG_AB,
    )
    # seg_v_1 是 b 段 → 從 m_b_v 來
    assert "[m_b_v]trim=20.000:50.000" in fc
    assert "[m_a_v]trim=0.000:20.000" in fc


def test_filter_complex_yt_multicam_audio_always_from_cam_a():
    """音訊永遠從 cam a（[m_a_a]）取，不論該段視訊是 a 或 b。"""
    fc = assemble.build_filter_complex_yt_multicam(
        BASE_CFG, main_dur=50.0, srt_rel="x.srt", segments=TWO_SEG_AB,
    )
    # 多段時用 asplit 明確切分（避免 ffmpeg auto-split 在大量段時吃幀截斷）
    assert "[m_a_a]asplit=2[m_a_a_0][m_a_a_1]" in fc
    assert "[m_a_a_0]atrim=0.000:20.000" in fc
    assert "[m_a_a_1]atrim=20.000:50.000" in fc


def test_filter_complex_yt_multicam_cam_b_applies_sync_offset():
    """cam b 視訊先做 setpts=PTS-offset/TB 對齊主時間軸，再 burn 字幕 + trim。"""
    fc = assemble.build_filter_complex_yt_multicam(
        BASE_CFG, main_dur=50.0, srt_rel="x.srt",
        segments=TWO_SEG_AB, sync_offset_b=1.25,
    )
    assert "[2:v]setpts=PTS-1.25/TB" in fc


def test_filter_complex_yt_multicam_subtitles_burned_on_both_cams():
    """字幕燒在 cam a 與 cam b 兩個 prep stream 上（不是 segment 後再燒）。"""
    fc = assemble.build_filter_complex_yt_multicam(
        BASE_CFG, main_dur=50.0, srt_rel="x.srt", segments=TWO_SEG_AB,
    )
    assert fc.count("subtitles=x.srt") == 2


def test_filter_complex_yt_multicam_with_crop_applied_to_both_cams():
    """crop_yt 同時套到 cam a 與 cam b（兩鏡頭同畫面）。"""
    cfg = {**BASE_CFG, "crop_yt": {"x": 0.1, "y": 0.05, "width": 0.8, "height": 0.9}}
    fc = assemble.build_filter_complex_yt_multicam(
        cfg, main_dur=50.0, srt_rel="x.srt", segments=TWO_SEG_AB,
    )
    # 1920 * 0.8 = 1536, 1080 * 0.9 = 972
    assert fc.count("crop=1536:972:192:54") == 2


def test_filter_complex_yt_multicam_same_cam_multi_segments_explicit_split():
    """同一 cam 出現 N 段時必須用明確 split=N。
    ffmpeg auto-split 在這種圖形下會吃幀 → 輸出只剩前 1-2 段（regression: 2026-06）。
    """
    segs = [
        {"cam": "a", "start": 0.0, "end": 5.0},
        {"cam": "a", "start": 5.0, "end": 10.0},
        {"cam": "a", "start": 10.0, "end": 15.0},
    ]
    fc = assemble.build_filter_complex_yt_multicam(
        BASE_CFG, main_dur=15.0, srt_rel="x.srt", segments=segs,
    )
    assert "[m_a_v]split=3[m_a_v_0][m_a_v_1][m_a_v_2]" in fc
    assert "[m_a_a]asplit=3[m_a_a_0][m_a_a_1][m_a_a_2]" in fc
    # 各段引用自己的 split 輸出
    assert "[m_a_v_0]trim=0.000:5.000" in fc
    assert "[m_a_v_1]trim=5.000:10.000" in fc
    assert "[m_a_v_2]trim=10.000:15.000" in fc


def test_filter_complex_reels_multicam_basic():
    """Reels multicam：1080×1920，無 intro/outro。"""
    fc = assemble.build_filter_complex_reels_multicam(
        BASE_CFG, main_dur=50.0, srt_rel="x.srt", segments=TWO_SEG_AB,
    )
    assert "scale=1080:1920" in fc
    # Reels 沒 intro/outro → 不該有 [v_intro] 之類
    assert "[v_intro]" not in fc
    assert "concat=n=2:v=1:a=1[main_v_raw][main_a_raw]" in fc
    # 最終輸出 [v][a]
    assert "[v]" in fc and "[a]" in fc


def test_filter_complex_reels_multicam_audio_from_cam_a():
    """Reels 也是 cam a 出音訊。"""
    fc = assemble.build_filter_complex_reels_multicam(
        BASE_CFG, main_dur=50.0, srt_rel="x.srt", segments=TWO_SEG_AB,
    )
    assert "[m_a_a]asplit=2[m_a_a_0][m_a_a_1]" in fc
    assert "[m_a_a_0]atrim=0.000:20.000" in fc
    assert "[m_a_a_1]atrim=20.000:50.000" in fc


# --- subtitle_style_reels：Reels 專用字幕風格分離 ---

def test_filter_complex_reels_uses_reels_style_when_present():
    """有 subtitle_style_reels 時，Reels 應該用 reels 那組（font_size / margin_v 不一樣）。"""
    cfg = {**BASE_CFG, "subtitle_style_reels": {
        "font_name": "F", "font_size": 80, "bold": 1,
        "primary_colour": "&H00FFFFFF", "outline_colour": "&H00000000",
        "border_style": 1, "outline": 3, "shadow": 1, "margin_v": 320,
    }}
    fc = assemble.build_filter_complex_reels(cfg, main_dur=50.0, srt_rel="x.srt")
    # Reels 專用值出現
    assert "FontSize=80" in fc
    assert "MarginV=320" in fc
    assert "Outline=3" in fc


def test_filter_complex_reels_falls_back_to_subtitle_style_when_reels_missing():
    """沒給 subtitle_style_reels → 回退到 subtitle_style（用 YT 那組值）。"""
    # BASE_CFG 只有 subtitle_style（font_size=28, margin_v=60）
    fc = assemble.build_filter_complex_reels(BASE_CFG, main_dur=50.0, srt_rel="x.srt")
    assert "FontSize=28" in fc
    assert "MarginV=60" in fc


def test_filter_complex_yt_ignores_subtitle_style_reels():
    """YT 分支不該被 subtitle_style_reels 污染。"""
    cfg = {**BASE_CFG, "subtitle_style_reels": {
        "font_name": "F", "font_size": 999, "bold": 1,
        "primary_colour": "&H00FFFFFF", "outline_colour": "&H00000000",
        "border_style": 1, "outline": 9, "shadow": 1, "margin_v": 999,
    }}
    fc = assemble.build_filter_complex_yt(cfg, main_dur=50.0, srt_rel="x.srt")
    # YT 仍用 subtitle_style 的 font_size=28
    assert "FontSize=28" in fc
    assert "FontSize=999" not in fc


def test_filter_complex_reels_multicam_uses_reels_style():
    """雙鏡頭 reels 也要讀 subtitle_style_reels。"""
    cfg = {**BASE_CFG, "subtitle_style_reels": {
        "font_name": "F", "font_size": 80, "bold": 1,
        "primary_colour": "&H00FFFFFF", "outline_colour": "&H00000000",
        "border_style": 1, "outline": 3, "shadow": 1, "margin_v": 320,
    }}
    fc = assemble.build_filter_complex_reels_multicam(
        cfg, main_dur=50.0, srt_rel="x.srt", segments=TWO_SEG_AB,
    )
    assert "FontSize=80" in fc
    assert "MarginV=320" in fc


# --- alignment 欄位：選用 ASS Alignment（2/5/8） ---


def test_build_style_string_omits_alignment_when_absent():
    """沒給 alignment → style str 不含 Alignment=（向後相容預設 2）。"""
    style = {
        "font_name": "F", "font_size": 28, "bold": 1,
        "primary_colour": "&H00FFFFFF", "outline_colour": "&H00000000",
        "border_style": 1, "outline": 2, "shadow": 1, "margin_v": 60,
    }
    s = assemble.build_style_string(style)
    assert "Alignment=" not in s


def test_build_style_string_emits_alignment_when_present():
    """alignment=5 → style str 含 Alignment=5（畫面正中央）。"""
    style = {
        "font_name": "F", "font_size": 80, "bold": 1,
        "primary_colour": "&H00FFFFFF", "outline_colour": "&H00000000",
        "border_style": 1, "outline": 3, "shadow": 1,
        "margin_v": 0, "alignment": 5,
    }
    s = assemble.build_style_string(style)
    assert "Alignment=5" in s
    assert "MarginV=0" in s


def test_filter_complex_reels_includes_alignment_when_set():
    """subtitle_style_reels 設 alignment=5 → reels filter 字幕落在 alignment=5。"""
    cfg = {**BASE_CFG, "subtitle_style_reels": {
        "font_name": "F", "font_size": 80, "bold": 1,
        "primary_colour": "&H00FFFFFF", "outline_colour": "&H00000000",
        "border_style": 1, "outline": 3, "shadow": 1,
        "margin_v": 0, "alignment": 5,
    }}
    fc = assemble.build_filter_complex_reels(cfg, main_dur=50.0, srt_rel="x.srt")
    assert "Alignment=5" in fc


# --- T23a Step 4c: prepare_assembly 雙鏡頭分流 ---


def test_prepare_assembly_yt_multicam_adds_cam_b_input(tmp_episode_full_multicam):
    """cameras.b 存在 → YT cmd 應有 5 個 -i（intro / camA / camB / outro img / outro audio）。"""
    plan = prepare_assembly(tmp_episode_full_multicam, output_kind="yt", force=True)
    i_count = sum(1 for a in plan["cmd"] if a == "-i")
    assert i_count == 5, f"YT multicam 應有 5 個 -i，目前 {i_count}"


def test_prepare_assembly_yt_multicam_filter_contains_cam_b_labels(tmp_episode_full_multicam):
    """YT multicam filter_complex 應包含 [m_b_v]（cam B 處理 stream）。"""
    plan = prepare_assembly(tmp_episode_full_multicam, output_kind="yt", force=True)
    fc_idx = plan["cmd"].index("-filter_complex")
    fc = plan["cmd"][fc_idx + 1]
    assert "[m_b_v]" in fc
    assert "[m_a_v]" in fc


def test_prepare_assembly_yt_multicam_applies_sync_offset(tmp_episode_full_multicam):
    """camera_sync_offset.b 應被注入 filter（cam B 的 setpts 位移）。"""
    plan = prepare_assembly(tmp_episode_full_multicam, output_kind="yt", force=True)
    fc_idx = plan["cmd"].index("-filter_complex")
    fc = plan["cmd"][fc_idx + 1]
    # fixture 給 sync_offset_b=1.25
    assert "setpts=PTS-1.25/TB" in fc


def test_prepare_assembly_yt_multicam_uses_segment_plan(tmp_episode_full_multicam):
    """sidecar 標卡 3 → b，前兩卡 a → 應切兩段（a 段 [0, 12), b 段 [12, raw_end]）。

    _v2.srt fixture：卡 1 (0-4.2)、卡 2 (4.2-12)、卡 3 (12-14)、卡 4 (14-22)。
    sidecar 只標 3=b → segments = [(a, 0, 12), (b, 12, 100)]（raw_dur=100，無 trim/del）。
    """
    plan = prepare_assembly(tmp_episode_full_multicam, output_kind="yt", force=True)
    fc_idx = plan["cmd"].index("-filter_complex")
    fc = plan["cmd"][fc_idx + 1]
    # 兩個 trim 段：a 段到 12，b 段從 12 開始
    assert "trim=0.000:12.000" in fc
    assert "trim=12.000:100.000" in fc


def test_prepare_assembly_reels_multicam_two_inputs(tmp_episode_full_multicam):
    """Reels multicam → 只有 2 個 -i（camA / camB），無 intro/outro。"""
    plan = prepare_assembly(tmp_episode_full_multicam, output_kind="reels", force=True)
    i_count = sum(1 for a in plan["cmd"] if a == "-i")
    assert i_count == 2, f"Reels multicam 應有 2 個 -i，目前 {i_count}"


def test_prepare_assembly_reels_multicam_filter_uses_b_input_index(tmp_episode_full_multicam):
    """Reels multicam cam B 是第 2 個輸入（[1:v]）。"""
    plan = prepare_assembly(tmp_episode_full_multicam, output_kind="reels", force=True)
    fc_idx = plan["cmd"].index("-filter_complex")
    fc = plan["cmd"][fc_idx + 1]
    # Reels：camA=[0], camB=[1] → [1:v]setpts=...
    assert "[1:v]setpts=PTS-1.25/TB" in fc


def test_prepare_assembly_single_cam_unchanged_when_no_cam_b(tmp_episode_full):
    """回歸：cameras.b 不存在 → 走原本單機路徑（無 [m_b_v]、4 個 -i）。

    單機 YT 4 個 -i = intro + main + outro_image(-loop) + outro_audio。
    """
    plan = prepare_assembly(tmp_episode_full, output_kind="yt", force=True)
    fc_idx = plan["cmd"].index("-filter_complex")
    fc = plan["cmd"][fc_idx + 1]
    assert "[m_b_v]" not in fc
    i_count = sum(1 for a in plan["cmd"] if a == "-i")
    assert i_count == 4, f"單機 YT 應有 4 個 -i，目前 {i_count}"


def test_prepare_assembly_yt_multicam_missing_cam_b_file_raises(tmp_episode_full_multicam):
    """cam B 路徑在 yaml 但檔案不存在 → AssembleError exit_code=3。"""
    from podcast_toolkit.assemble import AssembleError
    (tmp_episode_full_multicam / "01_母帶" / "測試集_camB.mp4").unlink()
    with pytest.raises(AssembleError) as exc:
        prepare_assembly(tmp_episode_full_multicam, output_kind="yt", force=True)
    assert exc.value.exit_code == 3
