# 分軌能量分講者管線規格（mic-diarize-spec）

> 目標：本地、零雲端、全自動。**文字永遠來自混音的一次轉錄；分軌只用來判定每個時間點是誰在講。** 換人處天然切卡，永久解決「全標同一人」「跨講者黏同卡」。

## 0. 已驗證事實（附檔案:行號）

- 混音逐字檔欄位（已讀範例集確認）：每個 word 為 `{"w":字, "start":秒, "end":秒, "seg":int, "p":float, "alp":float, "nsp":float, "spk":str}`。**欄位名是 `w` 不是 `word`**；`start`/`end` 單位為秒、單調遞增（實測 non-monotonic=0）。`spk` 目前整檔都是 `Mic1`（無鑑別力，必須用分軌能量重判）。頂層 dict keys＝`[mode,dir,limit,prompt,n_words,words]`。
- 讀分軌音檔可重用 `podcast_toolkit/vad_gate.py:138` `_read_pcm_mono(input_path, sample_rate) -> np.int16 mono`（ffmpeg → s16le mono，任意格式/聲道數皆可，stereo mix 也會被降 mono）。
- RMS 逐幀計算範式在 `podcast_toolkit/vad_gate.py:25` `detect_speech_frames`（int16→/32768→reshape→`sqrt(mean(f²))`）。本規格抽取其 RMS 段，不用其 threshold 布林化。
- speakers.json 寫檔格式＝`podcast_toolkit/cameras_io.py:30` `save(path, {int idx: str key})`，落檔為 `{"1":"a",...}`（`ensure_ascii=False, sort_keys=True, indent=2`）；讀取為 `cameras_io.py:19` `load`。**輸出必須沿用此格式與相容編輯器/cameras。**
- `Episode.output_v2_srt()`（`episode.py:60`）、`output_v2_speakers_json()`（`episode.py:104`）、`mic_paths()`（`episode.py:77`，回 `{key: 絕對路徑}`）、`main_srt()`（`episode.py:33`）。
- 備份慣例＝`web/transcribe_job.py:22` `_backup_existing_per_mic_outputs(ep)`：把 `_final_v2.srt`＋`.speakers.json` 複製成 `{stem}.{YYYYMMDD-HHMMSS}.bak{suffix}`。**沿用，不新建慣例。**
- 切卡既有規則在 `resegment.py:104-131`（貪婪合併：`gap>gapmax` / 超長 `maxlen`,`hardlen`+dangling 放寬 / `reaction_words` / `qend` 問號結尾 → 斷卡）。此邏輯內嵌在 `run()` 中、非純函式，且吃 SRT 段落非 word 陣列，**不可直接 import 重用**（見 §3）。
- Gemini 分軌現況：`web/transcribe_job.py:387` `_run_per_mic` → `gemini_subtitle.py:277` `transcribe_per_mic` → `srt_merge.run`。這條要被本地版取代。

> 註：本集 `mics: {a,b,c}` 對應的 track1~3 wav **尚未補齊**，不影響先寫程式與單元測試（測試用合成 wav，見 §7）。

---

## 1. 新模組 `podcast_toolkit/mic_diarize.py` 介面

分成純函式（易單測）＋一個 orchestrator `run`。所有時間單位秒、RMS 為 int16 正規化（/32768）後的線性值，dB＝`20*log10(rms+eps)`。

### 1.1 型別約定
- `Word = dict`，至少含 `w:str, start:float, end:float`（容忍多餘欄位）。
- `speaker_key: str`（`a`/`b`/`c`…，對齊 `mic_paths()` 的 key）。
- `Card = dict`：`{"start":float, "end":float, "text":str, "speaker":str, "word_span":(i0,i1)}`。

### 1.2 純函式簽名（TDD 主目標）

```
def load_words(words_json_path: Path) -> list[Word]
    # 讀 words.json，回 words 陣列（頂層 dict['words']）。容忍頂層直接是 list。
    # 不做時間單位換算（已是秒）。

def find_words_json(ep: Episode) -> Path | None
    # 定位混音逐字檔：ep.dir 下 glob "*_字幕_words.json"，取 mtime 最新；無則 None。
    # （repo 內無生產者，屬外部/上游產物，故用 glob 而非固定屬性。）

def compute_rms_envelope(
    samples: np.ndarray,      # int16 mono
    sample_rate: int,
    *, frame_ms: int = 20,
) -> tuple[np.ndarray, float]
    # 回 (rms_per_frame: float32 已 /32768, frame_sec)。
    # 直接沿用 vad_gate.detect_speech_frames 的 reshape+sqrt(mean(f²))，但回連續 RMS 值不布林化。

def rms_db_in_window(
    envelope: np.ndarray, frame_sec: float,
    start: float, end: float,
    *, offset: float = 0.0,
) -> float
    # 取 [start+offset, end+offset] 覆蓋到的 frame，回該段 RMS 的 dB（20log10）。
    # 視窗不足一幀時：至少取字中點所在幀（見 §8 風險①）。
    # 越界（offset 造成負索引或超尾）→ clip 到合法範圍；全空 → 回 -inf（用 NEG_INF 常數）。

def assign_speakers_per_word(
    words: list[Word],
    envelopes: dict[speaker_key, tuple[np.ndarray, float]],  # {key:(rms,frame_sec)}
    *, offsets: dict[speaker_key, float] | None = None,
    params: DiarizeParams,
) -> list[speaker_key | None]
    # 對每個字：各軌取 rms_db_in_window → argmax → 套 margin/遲滯/最短段規則。
    # 回與 words 等長的 speaker 陣列（None＝靜音/無主，見 §2）。純函式、不碰檔案。

def cards_from_assignments(
    words: list[Word],
    assignments: list[speaker_key | None],
    *, params: DiarizeParams,
) -> list[Card]
    # 依 §3 規則切卡：換人強制斷；同人內依長度/gap/問號/反應詞切。
    # 保證輸出無跨講者卡。text＝該卡 words 的 w 直接串接（無空格）。

def write_outputs(
    ep: Episode, cards: list[Card],
    *, backup: bool = True,
) -> tuple[Path, Path]
    # 備份既有 → 寫 _final_v2.srt（srt_io.serialize/seconds_to_srt_ts）
    #            + speakers.json（cameras_io.save，{idx:speaker}）。
    # 回 (srt_path, speakers_json_path)。
```

### 1.3 orchestrator

```
def run(ep: Episode, *, force: bool = False,
        progress: Callable[[str,str],None] | None = None) -> int
    # 1. mics = ep.mic_paths()；空 → 印錯訊、return 4（對齊 srt_merge.run 慣例）。
    # 2. words_path = find_words_json(ep)；無 → return 3。
    # 3. out 已存在且 not force → return 1（對齊既有覆寫守門）。
    # 4. 讀 params（§2 defaults + episode 覆寫）、讀各軌 envelope、assign、cards、write。
    # 5. 進度 phase ∈ {"read-mics","assign","segment","write","done"}（給 UI）。
    # 回 exit code（0 成功）。
```

### 1.4 參數物件

```
@dataclass
class DiarizeParams:
    frame_ms: int = 20
    margin_db: float = 3.0          # 冠軍需領先亞軍 ≥ margin 才採信，否則沿用前一字（遲滯）
    silence_floor_db: float = -45.0 # 全軌都低於此 → 該字 None（靜音/無主）
    min_turn_sec: float = 0.6       # 短於此的講者段併回鄰段（去抖，抗附和/串音）
    min_turn_words: int = 2         # 或字數門檻（取 sec 與 words 較嚴者觸發併回）
    hysteresis_db: float = 1.5      # 已在講者 X，換人需比 X 高 hysteresis 才切（黏著）
    # 切卡（沿用 defaults.yaml resegment 的值域，避免雙套標準）
    maxlen: int = 17
    hardlen: int = 23
    gapmax: float = 0.6
    qend_chars: str = "嗎呢"
    reaction_words: tuple[str,...] = (...)   # 讀 cfg['resegment']['reaction_words']
    dangle_endings: tuple[str,...] = (...)   # 讀 cfg['resegment']['dangle_endings']
```

---

## 2. 逐字判講者演算法

對每個字 `wi=[start,end]`：
1. 各軌 `d[k] = rms_db_in_window(env[k], frame_sec, start, end, offset=offsets[k])`。offset 語意：`offsets[k]` 是「該軌相對混音時間軸的偏移秒數」，取窗時 `mic_time = mix_time + offset`（預設 0；本集三檔逐 sample 對齊）。
2. `champion = argmax(d)`，`runner = 次高`。
3. **靜音/無主**：若 `d[champion] < silence_floor_db` → `assignment=None`（不進任何卡，時間留白）。
4. **margin 門檻**：若 `d[champion] - d[runner] < margin_db` → 視為不確定 → **沿用前一個已定講者**（遲滯，抗串音；串音時兩軌 dB 接近，margin 擋掉誤判）。若無前值則暫記 champion。
5. **遲滯/黏著**：若前一字講者是 `prev` 且 `prev != champion`，需 `d[champion] - d[prev] >= hysteresis_db` 才換人，否則維持 `prev`（避免逐字抖動）。
6. **最短講者段**（第二遍後處理，在 `assign_speakers_per_word` 尾段）：掃出連續同講者的 run；若某 run 時長 `< min_turn_sec` **且** 字數 `< min_turn_words`，把它併入左右鄰段中 dB 更接近者（或直接併左）。此步吸收「對、嗯」等短附和造成的插斷。`None` 段不參與併回。

**重疊說話**：不特別建模；因文字來自混音單一序列，同一字只屬一位講者。重疊時能量較高者（真正主講）勝出即可；margin+hysteresis 保證不會因對方串音而誤搶。

**offset 套用**：僅在 `rms_db_in_window` 取窗時平移，不改 word 時間戳、不改輸出 SRT 時間（SRT 永遠用混音時間）。

**可覆寫清單**（`episode.yaml` → `mic_diarize:` 區塊覆寫 defaults）：`margin_db, silence_floor_db, min_turn_sec, min_turn_words, hysteresis_db, frame_ms`，以及每軌 `offsets: {a:0, b:0, c:0}`。切卡類參數繼承 `resegment:` 區塊（不重複定義）。

**defaults.yaml 需新增區塊**（規格，不寫實作）：
```
mic_diarize:
  frame_ms: 20
  margin_db: 3.0
  silence_floor_db: -45.0
  min_turn_sec: 0.6
  min_turn_words: 2
  hysteresis_db: 1.5
  offsets: {}          # 空＝各軌 offset 0；未來別台錄音機時逐軌填秒數
```
`Episode.__init__` 走 `config.merge`，故新區塊自動可被 episode 覆寫，無需改 config.py（沿用 `episode.py:16` 的 merge 流程）。

---

## 3. 切卡規則（`cards_from_assignments`）

保證：**換人一定斷卡**（不同 `speaker` 的字絕不同卡）。實作分兩層：

1. **先按講者切段**：把連續同 `speaker`（非 None）的字聚成候選段。`None` 字不進卡（形成天然間隙）。
2. **同人段內再切**（重寫規則，不 import resegment；理由：`resegment.py:104` 的合併邏輯內嵌於 `run()`、吃 SRT 段落物件而非 word 陣列，簽名不相容）。同人段內從左到右貪婪累積字，遇下列任一觸發斷卡：
   - 累積字數 > `maxlen`；但若 ≤ `hardlen` 且當前尾字命中 `dangle_endings` → 放寬不斷（沿用 `resegment.py:110-112` 語意）。
   - 與前一字 `gap = word.start - prev.end > gapmax`。
   - 已滿 5 字且尾字 ∈ `qend_chars`（問號收句，對齊 `resegment.py:122`）。
   - 當前累積整串 ∈ `reaction_words`（反應詞獨立成卡）。

`Card.start = 首字 start`，`Card.end = 末字 end`，`text = "".join(w for w in span)`（中文無空格）。

**與 resegment 的關係**：本管線的字幕文字**不再經 resegment**（resegment 是給 Gemini/Breeze 全序列 SRT 用；此處逐字已帶時間與講者，直接切）。若日後要共用長句細切，可在 §8 標記為後續重構點。

---

## 4. 輸出

- `_final_v2.srt`：用 `srt_io.seconds_to_srt_ts`（`srt_io.py:71`）與 `srt_io.serialize`（`srt_io.py:148`）產出，idx 1-based 連續、依 start 升冪。
- `speakers.json`（`ep.output_v2_speakers_json()`）：`cameras_io.save(path, {card_idx: speaker_key})`（`cameras_io.py:30`），格式與現有 srt_merge 產物**逐位元相容**，cameras/編輯器無需改。
- **覆寫前備份**：呼叫 `transcribe_job._backup_existing_per_mic_outputs(ep)`（`transcribe_job.py:22`）或在 `write_outputs` 內複製同款 `{stem}.{stamp}.bak{suffix}`。`force=False` 且輸出存在 → `run` 提早 return 1（與 srt_merge/resegment 一致）。

---

## 5. 接線（episode.yaml 有 mics 就走本地分軌，取代 Gemini）

改動點（檔案:行號 → 動作）：

1. `web/transcribe_job.py:387` `_run_per_mic`：把
   `from podcast_toolkit import gemini_subtitle, srt_merge`
   → 改為 `from podcast_toolkit import mic_diarize`；
   移除 `transcribe_per_mic(...)` + 隨後的 `srt_merge.run` 兩段（`transcribe_job.py:404-445`），改為單一 `mic_diarize.run(ep, force=force, progress=on_mic_progress)`。備份改呼叫 `mic_diarize` 內建（或保留 `_backup_existing_per_mic_outputs` 呼叫）。`on_mic_progress` 的 phase 詞彙由 vad/gemini/done 改為 §1.3 的 phase 集（progress callback 簽名不變，UI 顯示字串調整）。
2. `cli.py:92` `cmd_merge_per_mic` / `cli.py:12` `cmd_subtitle`（`--per-mic` 分支在 `gemini_subtitle.run` 內）：新增或改寫 `cmd_diarize`（或讓 `subtitle --per-mic` 改呼叫 `mic_diarize.run`）。建議新增子命令 `pd = sub.add_parser("diarize", ...)`（`cli.py:135` build_parser 區塊內），`func=cmd_diarize`，args＝`path`,`--force`。
3. `web/routes/transcribe.py:103` `post_transcribe_per_mic`：移除 `api_key = cfg.get("gemini_api_key")` 檢查（`transcribe.py:117`，本地不需 key），`start_per_mic_job` 內部走本地管線即可（job 層改在 §5.1）。
4. `web/transcribe_job.py` `start_per_mic_job`（`transcribe_job.py:~360-386`）：spawn 的 worker 由 `_run_per_mic` 保留但內部改本地；不需 speakers 子集邏輯外的更動。

**判斷入口**：`ep.mic_paths()` 非空即走 `mic_diarize`（沿用既有「有 mics＝分軌」判準，`episode.py:77`、`srt_merge.py:71`）。

### 5.1 相依前置
本地分軌要求混音 words.json 已存在（由既有混音轉錄產出）。`mic_diarize.run` 找不到 words.json → return 3 並提示「先跑混音轉錄」。UI 端在無 words.json 時應 disable 分軌按鈕（`routes/transcribe.py` 可加 precheck，非必須）。

---

## 6. Gemini 退場範圍

| 對象 | 動作 | 理由 |
|---|---|---|
| `gemini_subtitle.py:277` `transcribe_per_mic` | **移除** | 被 `mic_diarize.run` 取代 |
| `gemini_subtitle.py` 其餘（`run` 單檔混音轉錄、prompt 組裝、`_config_gemini_key` `gemini_subtitle.py:31`、`format_glossary_lines`、`_PUNCT_PATTERN`） | **保留休眠** | 混音「單檔」Gemini 轉錄仍是既有 provider 之一（`transcribe.py:615` PROVIDERS 有 gemini）；且 `web/transcribe.py:257,791` 仍 import 這些符號 |
| `web/transcribe_job.py` 內 `from ... import gemini_subtitle`（`transcribe_job.py:389`） | **移除該 import** | `_run_per_mic` 不再用 |
| `web/routes/transcribe.py:62,67,117` 的 `gemini_api_key` 對 **per-mic** 的檢查 | **移除（僅 per-mic 分支）** | 本地無需 key；但 `/api/transcribe`（單檔）的 gemini provider 檢查**保留** |
| `defaults.yaml:42` `per_mic:`（vad_threshold 等） | **保留休眠或改註解** | mic_diarize 不用 VAD gate（不再壓串音餵雲端）；標「僅舊 Gemini 分軌用，已停用」 |
| config `gemini_api_key`（全域 `~/.podcast-toolkit/config.json`） | **保留 optional** | 單檔 gemini provider、proofread gemini 後援仍可用 |
| `defaults.yaml:48` `gemini:` 區塊 | **保留** | 單檔轉錄仍用 |

原則：**只切 per-mic 這條 Gemini 依賴**，單檔混音轉錄與 proofread 的 Gemini 後援完全不動，零破壞。

---

## 7. 測試清單（`tests/test_mic_diarize.py`）

合成工具（fixture，不落實際檔案外）：造已知 RMS pattern 的 mono wav（用 ffmpeg 或 numpy→`vad_gate._write_wav`，`vad_gate.py:154`），與合成 words（`[{"w":"字","start":..,"end":..}]`）。

單元測試案例（每條對應純函式）：

1. **`compute_rms_envelope` 正確性**：對已知振幅正弦/常數 mono wav，RMS≈解析值（誤差 <5%），frame_sec＝frame_ms/1000。
2. **`rms_db_in_window` 視窗**：字長 < 一幀時仍取中點幀；offset 平移正確；越界 clip 不 crash、全空回 NEG_INF。
3. **純輪流（N=2）**：A 說 0–2s（A 軌高、B 軌 -inf），B 說 2–4s → assignments 完全正確，切出 2 卡、無跨講者。
4. **串音不誤判**：A 主講且 B 軌有 −20dB 串音（低於 A 但非靜音）→ margin_db=3 擋掉，全段判 A。
5. **重疊**：A、B 同時但 A 能量高 → 該段字全歸 A（champion 勝出）。
6. **短附和不抖動**：A 長句中插入 1 個「對」的字、B 軌該字瞬間高 → `min_turn_sec/min_turn_words` 併回 A，不切出孤立 B 卡。
7. **offset 套用**：words 依混音時間、B 軌整體延遲 +0.5s，設 `offsets[b]=0.5` → 判講者正確；設 0 → 錯判（反向驗證 offset 生效）。
8. **N=3 軌**：a/b/c 三軌各自主講段 → 三講者都出現、順序正確、speakers.json 含 a/b/c。
9. **換人切卡**：A→B 交界處無論長度一律斷卡（斷點在講者變化的字邊界）。
10. **長句同人再切**：A 連講 40 字無標點 → 依 maxlen/hardlen 切多卡，皆標 A；含 dangle 尾字時放寬到 hardlen。
11. **靜音/無主**：全軌低於 silence_floor → 該段無卡、時間留白，不誤塞給任一講者。
12. **輸出格式**：`write_outputs` 產的 speakers.json 用 `cameras_io.load` 讀回＝`{idx:key}`；SRT 可被 `srt_io.parse` 解析、idx 連續、無跨講者卡（斷言相鄰卡 speaker 對照 speakers.json 在換人處變化）。
13. **接線煙測**（可選，mock envelope）：`mic_diarize.run(ep)` 對缺 words.json return 3、缺 mics return 4、輸出存在且 force=False return 1。

> track1~3 未補齊**不影響**上述測試：全部用合成 wav + 合成 words，不依賴實檔。

---

## 8. 風險與邊角

1. **word 時間戳精度 vs RMS 視窗**：字長常 0.1–0.5s，20ms 幀足夠（每字 5–25 幀）；但 0.1s 短字只 5 幀，遇軌間微 offset 敏感。緩解：`rms_db_in_window` 字長不足一幀時退回取中點幀；margin_db 給 3dB 緩衝。
2. **48kHz stereo mix 讀取**：`_read_pcm_mono`（`vad_gate.py:138`）強制 `-ac 1`，stereo mix 自動降 mono、任意取樣率統一，記憶體/對齊無虞。分軌各軌獨立讀。
3. **記憶體/效能**：52 分 48kHz mono int16 ≈ 300MB/軌，三軌 ≈ 900MB float32 envelope（降到 20ms 幀後 envelope 僅 ~156k float＝可忽略）。**建議只保留 envelope、讀完即釋放 raw samples**（不要三軌 raw 同時駐留）。逐軌讀→算 envelope→丟 raw。
4. **words.json 定位不穩**：repo 無生產者，靠 `*_字幕_words.json` glob；若多檔取最新 mtime。若上游改檔名需同步 `find_words_json`。已知 `spk` 欄位無用（全 Mic1），**不可**拿來當講者答案（易誤導）。
5. **offset 未知時的別台情境**：本集 offset=0 已驗證；別台錄音機需先用 `audio_align.compute_lag_seconds`（`audio_align.py:60`）估 lag 填入 `offsets`。規格預留欄位但不自動估（避免誤對齊；列為後續）。
6. **切卡與 resegment 雙軌**：本管線繞過 resegment，長句細切/錯字修正規則需自帶；若未來要與 resegment 對齊字數體感，是重構點（把 `resegment.py:104` 合併邏輯抽成吃 word 陣列的純函式，兩處共用）。
7. **None（靜音）留白**：混音有字但三軌都靜音（罕見，如遠場拾音）→ 該字被丟棄、字幕缺字。緩解：`silence_floor_db` 設保守（-45），並在 run 統計「丟棄字數」印出供人工檢查。
