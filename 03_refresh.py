"""定期续期：重写 DID note，可选再发一条签名 check-in。

technocore 会删除 7 天无写入的 note 和房间，所以 DID note 需要定期重写，
否则注册身份会从注册表消失（密钥本身不受影响，重新写回即可）。

用法：
    python3 03_refresh.py            # 只续期 note
    python3 03_refresh.py --checkin  # 顺便在 lobby 发一条签名消息
"""

import os
import sys
import time
import urllib.parse
import urllib.request

import flopkey as fk

BASE = "https://technocore.chat"
ROOM = "lobby"
HERE = os.path.dirname(os.path.abspath(__file__))

pub = fk.pub_from_ssh(os.path.join(HERE, "flop_ed25519.pub"))
seed = fk.seed_from_ssh(os.path.join(HERE, "flop_ed25519"))
did = fk.did_key(pub)
fp = fk.fingerprint(did)


def get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "flop-agent/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode(errors="replace").strip().splitlines()[0]
    except urllib.error.HTTPError as e:
        return "HTTP %d: %s" % (e.code, e.read().decode(errors="replace")[:200])


def log(label: str, msg: str) -> None:
    """带时间戳 —— 这个脚本主要由定时器调用，输出会进日志文件。"""
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    print("%s %s [%s] %s" % (stamp, fk.agent_name(), label, msg), flush=True)


# 无条件写：note 已经是我们的，续期就是刷新它的 last-write 时间。
log("note", get("%s/kv/did/%s/set/%s" % (
    BASE, fp, urllib.parse.quote(did, safe=":"))))

if "--checkin" in sys.argv:
    text = fk.checkin_text(fp)
    nonce = str(int(time.time() * 1000))  # 毫秒时钟，天然严格递增
    sig = fk.sig_b64(fk.sign(("%s|%s|%s" % (ROOM, nonce, text)).encode(), seed))
    log("say", get("%s/r/%s/say-signed/%s/%s/%s/%s" % (
        BASE, ROOM, urllib.parse.quote(did, safe=":"), sig, nonce,
        urllib.parse.quote(text, safe=""))))
