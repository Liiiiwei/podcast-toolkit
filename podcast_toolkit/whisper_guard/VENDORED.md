# Vendored: whisper-guard

- 來源：https://github.com/vulture-s/whisper-guard
- 版本：v0.2.0（見 `__init__.py` 的 `__version__`）
- 授權：MIT
- 取得日期：2026-05-22

## 為什麼用 vendored，而不是 pip install

本機 Python 是 Homebrew 安裝（externally-managed，PEP 668），`pip install`
會被擋。whisper-guard 又是純標準庫的三檔小套件，所以直接複製進專案，
`resegment.py` 同目錄即可 `import`——零環境變動、跟著資料夾搬移也照樣能跑。

三個檔案與上游完全一致，未做任何修改。

## 日後若想改回 pip 安裝（可選）

建一個 venv 後安裝，再刪掉本資料夾即可：

```bash
cd "/Users/vincentsia/Downloads/20260417 過嗨乳牛/04_工作檔"
python3 -m venv .venv && source .venv/bin/activate
pip install whisper-guard
rm -rf whisper_guard
```

## 內容

| 檔案 | 作用 |
|------|------|
| `__init__.py` | 套件入口 |
| `guard.py`    | 4 層反幻覺防護（靜音 / 弱片段 / 重複 / 字元迴圈） |
| `vocab.py`    | hotwords 提示詞、疊字填充詞過濾 |
