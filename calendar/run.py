import sys
import webbrowser
from pathlib import Path

POSTER_ROOT = Path(__file__).resolve().parent.parent
if str(POSTER_ROOT) not in sys.path:
    sys.path.insert(0, str(POSTER_ROOT))

import uvicorn  # noqa: E402

if __name__ == "__main__":
    url = "http://127.0.0.1:8767/"
    print(f"календарь: {url}", flush=True)
    webbrowser.open(url)
    uvicorn.run("calendar.app.main:app", host="127.0.0.1", port=8767, reload=False)
