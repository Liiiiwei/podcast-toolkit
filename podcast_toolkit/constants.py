"""跨層共用的純常數。

無任何 podcast_toolkit 相依（leaf module），讓 core（config.py）與 web 層
（web/shared.py、web/episode_io.py、web/routes/*）共用同一份來源，避免各處
inline 重複而漂移。core ↔ web 都能 import 它，不會造成循環依賴。
"""

# web UI 寫的集詞庫 sidecar 檔名。
EPISODE_GLOSSARY_FILENAME = ".glossary.json"

# 外接音檔常見副檔名（小寫比對，相機/錄音機常出大寫）；
# 同時也是可在瀏覽器直接預覽的音檔集合。
AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".flac", ".aac", ".ogg", ".opus"}
