"""刪段時間版（cuts）：cfg 載入 / 遷移、字幕過濾、與舊 idx 路徑的等價。"""
from pathlib import Path

from podcast_toolkit import assemble, srt_io
from podcast_toolkit.segment_plan import build_segment_plan


def _cards(*rows):
    return [{"idx": i, "start": s, "end": e, "text": "x"} for i, s, e in rows]


CARDS = _cards((1, 0.0, 4.0), (2, 4.0, 8.0), (3, 8.0, 12.0))


def test_config_merge_passes_cuts_through():
    """config.merge 必須透傳 episode 的 cuts，否則 cfg['cuts'] 永遠 None、cuts 路徑形同未接。"""
    from podcast_toolkit import config
    defaults = config.load_defaults()
    assert config.merge(defaults, {"cuts": [[4.0, 8.0]]})["cuts"] == [[4.0, 8.0]]
    assert config.merge(defaults, {})["cuts"] == []  # 沒設 → 空 list


def test_cuts_new_format_list_pairs():
    cfg = {"cuts": [[4.0, 8.0]]}
    assert assemble.cut_intervals_from_cfg(cfg, CARDS) == [(4.0, 8.0)]


def test_cuts_new_format_dicts():
    cfg = {"cuts": [{"start": 4.0, "end": 8.0}, {"start": 1.0, "end": 2.0}]}
    # 回傳依 start 排序
    assert assemble.cut_intervals_from_cfg(cfg, CARDS) == [(1.0, 2.0), (4.0, 8.0)]


def test_legacy_deletions_idx_migrated_via_cards():
    """舊 deletions[idx] → 用 cards 換算成時間區間（自動遷移讀取）。"""
    cfg = {"deletions": [2]}
    assert assemble.cut_intervals_from_cfg(cfg, CARDS) == [(4.0, 8.0)]


def test_cuts_takes_priority_over_legacy_deletions():
    cfg = {"cuts": [[1.0, 2.0]], "deletions": [3]}
    assert assemble.cut_intervals_from_cfg(cfg, CARDS) == [(1.0, 2.0)]


def test_deletions_override_empty_means_no_cuts():
    """overlay「保留全部內容」傳 [] → 無刪段，蓋過 cfg。"""
    cfg = {"deletions": [2], "cuts": [[4.0, 8.0]]}
    assert assemble.cut_intervals_from_cfg(cfg, CARDS, deletions_override=[]) == []


def test_deletions_override_idx_list():
    cfg = {"cuts": [[1.0, 2.0]]}
    assert assemble.cut_intervals_from_cfg(cfg, CARDS, deletions_override=[3]) == [(8.0, 12.0)]


def test_filter_srt_by_intervals(tmp_path: Path):
    src = tmp_path / "a.srt"
    src.write_text(srt_io.serialize(CARDS), encoding="utf-8")
    dst = tmp_path / "b.srt"
    assemble.filter_srt_by_intervals(src, dst, [(4.0, 8.0)])
    kept = srt_io.parse(dst.read_text(encoding="utf-8"))
    texts_starts = [(c["start"]) for c in kept]
    # 卡2 (start 4.0) 落在 (4,8) → 移除；卡1、卡3 保留
    assert texts_starts == [0.0, 8.0]


# ── cut_pad：刪段往前後吃掉間隙雜音（夾在鄰卡邊界內）──────────────────────
# 有間隙的卡：卡1[0,3] (間隙) 卡2[5,8] (間隙) 卡3[10,13]
GAPPED = _cards((1, 0.0, 3.0), (2, 5.0, 8.0), (3, 10.0, 13.0))


def test_cut_pad_zero_unchanged():
    """cut_pad=0（或沒設）→ 維持逐卡區間、不延伸、連刪不併間隙（向後相容）。"""
    assert assemble.cut_intervals_from_cfg({"deletions": [1, 2]}, GAPPED) == [(0.0, 3.0), (5.0, 8.0)]


def test_cut_pad_extends_into_gap_clamped_to_neighbor():
    """刪卡2，pad 往前後吃間隙；夾在卡1尾/卡3頭內。"""
    out = assemble.cut_intervals_from_cfg({"deletions": [2], "cut_pad": 0.5}, GAPPED)
    assert out == [(4.5, 8.5)]


def test_cut_pad_larger_than_gap_clamps_to_neighbor_speech():
    """pad 比間隙大 → 夾到鄰卡語音邊界，不咬進保留語音。"""
    out = assemble.cut_intervals_from_cfg({"deletions": [2], "cut_pad": 5.0}, GAPPED)
    assert out == [(3.0, 10.0)]  # 卡1尾 3.0 ~ 卡3頭 10.0，整個間隙吃掉


def test_cut_pad_first_card_clamps_left_to_zero():
    out = assemble.cut_intervals_from_cfg({"deletions": [1], "cut_pad": 0.5}, GAPPED)
    assert out == [(0.0, 3.5)]


def test_cut_pad_last_card_no_right_neighbor():
    """最後一張卡：右側沒有鄰卡 → 不往片尾外吃（右界 = 卡尾本身）。"""
    out = assemble.cut_intervals_from_cfg({"deletions": [3], "cut_pad": 0.5}, GAPPED)
    assert out == [(9.5, 13.0)]


def test_cut_pad_fill_to_neighbor_with_large_pad():
    """預設大 cut_pad（吃滿間隙）：刪中間卡 → 間隙全吃到兩鄰卡語音邊界、不咬語音；
    首/尾卡那一側沒鄰卡 → 不往片頭/片尾外吃。"""
    # 刪卡2 → 吃滿 [3,10]（卡1尾~卡3頭）
    assert assemble.cut_intervals_from_cfg({"deletions": [2], "cut_pad": 3600}, GAPPED) == [(3.0, 10.0)]
    # 刪卡3（最後）→ 左吃到卡2尾 8.0、右不外吃（13.0）
    assert assemble.cut_intervals_from_cfg({"deletions": [3], "cut_pad": 3600}, GAPPED) == [(8.0, 13.0)]
    # 刪卡1（最前）→ 左不外吃（0.0）、右吃到卡2頭 5.0
    assert assemble.cut_intervals_from_cfg({"deletions": [1], "cut_pad": 3600}, GAPPED) == [(0.0, 5.0)]


def test_cut_pad_consecutive_deletes_merge_through_gap():
    """連刪卡1+卡2（中間間隙沒有保留卡）→ 併成一段一起砍 + pad。"""
    out = assemble.cut_intervals_from_cfg({"deletions": [1, 2], "cut_pad": 0.5}, GAPPED)
    assert out == [(0.0, 8.5)]


def test_cut_pad_kept_card_between_deletes_not_merged():
    """刪卡1+卡3、保留卡2 → 不併（間隙有保留卡），各自 pad 夾到卡2邊界。"""
    out = assemble.cut_intervals_from_cfg({"deletions": [1, 3], "cut_pad": 0.5}, GAPPED)
    assert out == [(0.0, 3.5), (9.5, 13.0)]  # 卡3 為末卡、右側不外吃


def test_config_merge_cut_pad_default_and_override():
    from podcast_toolkit import config
    defaults = config.load_defaults()
    assert config.merge(defaults, {})["cut_pad"] == defaults.get("cut_pad")  # 用 defaults
    assert config.merge(defaults, {"cut_pad": 0})["cut_pad"] == 0.0  # episode 明確關閉
    assert config.merge(defaults, {"cut_pad": 0.4})["cut_pad"] == 0.4


def test_cut_pad_flush_kept_card_between_deletes_preserved():
    """[對抗式驗證抓到的關鍵 bug] 連續(無間隙)字幕、刪卡1+卡3、保留卡2(flush：start==前段尾)，
    pad>0 不可把兩刪段跨過卡2併掉、靜默刪除保留卡。"""
    cards = _cards((1, 0.0, 4.0), (2, 4.0, 8.0), (3, 8.0, 12.0))
    out = assemble.cut_intervals_from_cfg({"deletions": [1, 3], "cut_pad": 0.3}, cards)
    # 卡2[4,8] 必須完整保留：兩段、第一段止於 4.0、第二段始於 8.0（不可併成單段 (0,12)）
    assert len(out) == 2
    assert out[0] == (0.0, 4.0)
    assert out[1][0] == 8.0
    segs = build_segment_plan(cut_intervals=out, cam_transitions=[], main_dur=12.0)
    assert (4.0, 8.0) in [(s["start"], s["end"]) for s in segs]  # 卡2 影像段還在


def test_cut_pad_three_segment_flush_keeps_alternating_kept():
    """flush 連續、三段交錯保留：刪 1/3/5、保留 2/4，不可全併。"""
    cards = _cards((1, 0.0, 2.0), (2, 2.0, 4.0), (3, 4.0, 6.0), (4, 6.0, 8.0), (5, 8.0, 10.0))
    out = assemble.cut_intervals_from_cfg({"deletions": [1, 3, 5], "cut_pad": 0.5}, cards)
    assert len(out) == 3  # 保留卡2、卡4 → 三段不相連
    assert out[0] == (0.0, 2.0)
    assert out[1] == (4.0, 6.0)
    assert out[2][0] == 8.0


def test_cut_pad_midcard_cut_does_not_eat_kept_speech():
    """[對抗式驗證抓到的 bug] 手動 cut 落在保留卡中間，pad 不可咬進該卡存活語音（連超大 pad 也不行）。"""
    assert assemble.cut_intervals_from_cfg({"cuts": [[6.0, 7.0]], "cut_pad": 0.5}, GAPPED) == [(6.0, 7.0)]
    assert assemble.cut_intervals_from_cfg({"cuts": [[6.0, 7.0]], "cut_pad": 999.0}, GAPPED) == [(6.0, 7.0)]


def test_cut_pad_normalizes_inverted_and_drops_zero_length():
    """手動 cuts 打錯：start>end 修正成 (min,max)、零長度丟棄。"""
    out = assemble.cut_intervals_from_cfg({"cuts": [[9.0, 4.0], [3.0, 3.0]], "cut_pad": 0.1}, GAPPED)
    assert len(out) == 1          # (3,3) 零長丟掉、(9,4) 修正後只剩一段
    assert out[0][0] < out[0][1]  # 反置已修正成正向


def test_equivalence_legacy_deletions_vs_cuts():
    """同一刪段：舊 idx 路徑 與 時間版 cuts 路徑 → segment plan 一字不差。"""
    # 舊：deletions=[2] 經 cut_intervals_from_cfg 換算
    cuts_from_legacy = assemble.cut_intervals_from_cfg({"deletions": [2]}, CARDS)
    segs_legacy = build_segment_plan(
        cut_intervals=cuts_from_legacy, cam_transitions=[], main_dur=12.0,
    )
    # 新：直接給等價時間區間
    segs_cuts = build_segment_plan(
        cut_intervals=[(4.0, 8.0)], cam_transitions=[], main_dur=12.0,
    )
    assert segs_legacy == segs_cuts == [
        {"cam": "a", "start": 0.0, "end": 4.0},
        {"cam": "a", "start": 8.0, "end": 12.0},
    ]
