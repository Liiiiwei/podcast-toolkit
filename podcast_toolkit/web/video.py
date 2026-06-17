"""影片 / 音檔 Range streaming，回傳 starlette Response。"""
from __future__ import annotations
from pathlib import Path
import re

from starlette.responses import FileResponse, Response, StreamingResponse


_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


def range_response(path: Path, range_header: str | None, media_type: str = "video/mp4"):
    size = path.stat().st_size

    if not range_header:
        # no-store：編輯流程會換掉/就地重壓鏡頭影片檔，瀏覽器若快取住舊檔，換來源後預覽
        # 還會播到舊畫面（得清快取/開無痕才看得到新檔）。媒體都從本機磁碟服務，不快取無妨。
        return FileResponse(
            path, media_type=media_type, headers={"cache-control": "no-store"}
        )

    m = _RANGE_RE.match(range_header.strip())
    if not m:
        return Response(status_code=416)

    start_s, end_s = m.group(1), m.group(2)
    start = int(start_s) if start_s else 0
    end = int(end_s) if end_s else size - 1

    if start >= size or start < 0 or end < start:
        return Response(
            status_code=416,
            headers={"content-range": f"bytes */{size}"},
        )
    end = min(end, size - 1)
    length = end - start + 1

    def stream():
        with path.open("rb") as f:
            f.seek(start)
            remaining = length
            chunk = 64 * 1024
            while remaining > 0:
                data = f.read(min(chunk, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data

    return StreamingResponse(
        stream(),
        status_code=206,
        media_type=media_type,
        headers={
            "content-range": f"bytes {start}-{end}/{size}",
            "content-length": str(length),
            "accept-ranges": "bytes",
            # 同上：換鏡頭來源 / 就地重壓後，避免 range 請求吃到舊快取
            "cache-control": "no-store",
        },
    )
