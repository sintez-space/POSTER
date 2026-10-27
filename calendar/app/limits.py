import json

from .paths import SITES_JSON

NETWORKS = ("instagram", "tiktok", "youtube", "vk")

# YouTube title — не char_limit в sites.json
YOUTUBE_TITLE_LIMIT = 100


def load_limits():
    data = json.loads(SITES_JSON.read_text(encoding="utf-8"))
    out = {}
    for net in NETWORKS:
        item = data.get(net, {})
        out[net] = {
            "char_limit": int(item.get("char_limit") or 4000),
            "ready": bool(item.get("ready")),
        }
    out["youtube_title"] = {"char_limit": YOUTUBE_TITLE_LIMIT, "ready": out["youtube"]["ready"]}
    return out
