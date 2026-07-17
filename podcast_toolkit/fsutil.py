"""檔案系統小工具：原子寫入。

episode.yaml / _v2.srt / config.json 這類「壞掉就整集毀」的設定與字幕檔，
直接 write_text 途中若遇到磁碟滿、crash、斷電，會留下截斷的半成品。
統一改走「同目錄 tmp 檔 → fsync → os.replace」：replace 在同一檔案系統上
是原子操作，原檔要嘛完整保留、要嘛整份換新，不會出現半寫狀態。
"""
from __future__ import annotations

import os
from pathlib import Path


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """把 text 原子寫入 path：先寫同目錄 tmp 檔，fsync 後 os.replace 換上。

    寫入途中出錯（例外、磁碟滿）時原檔內容不受影響，tmp 檔會被清掉。
    tmp 檔名帶 pid，避免多進程同時寫同一檔互相清掉對方的 tmp。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    try:
        with open(tmp, "w", encoding=encoding) as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        # replace 成功後 tmp 已不存在；失敗時把半寫殘留清掉
        try:
            tmp.unlink()
        except OSError:
            pass
