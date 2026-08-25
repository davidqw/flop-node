"""定期续期：保住 DID note，可选再发一条签名 check-in。

technocore 会删除 7 天无写入的 note 和房间，所以 DID note 需要定期重写，
否则注册身份会从注册表里消失（密钥本身不受影响，重新写回即可）。

约定位置 /kv/did/ 已经写满 5120 条，新节点会自动落到备用 namespace，
见 flopnote.py。

用法：
    python3 03_refresh.py            # 只续期 note
    python3 03_refresh.py --checkin  # 顺便在 lobby 发一条签名消息
"""

import os
import sys
import time
import urllib.parse

import flopkey as fk
import flopnote

ROOM = "lobby"
HERE = os.path.dirname(os.path.abspath(__file__))

pub = fk.pub_from_ssh(os.path.join(HERE, "flop_ed25519.pub"))
seed = fk.seed_from_ssh(os.path.join(HERE, "flop_ed25519"))
did = fk.did_key(pub)
fp = fk.fingerprint(did)


def log(label: str, msg: str) -> None:
    """带时间戳 —— 这个脚本主要由定时器调用，输出会进日志文件。"""
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    print("%s %s [%s] %s" % (stamp, fk.agent_name(), label, msg), flush=True)


ns, how = flopnote.ensure_note(did, fp)
if ns is None:
    log("note", "写不进任何 namespace —— %s" % how)
    sys.exit(1)
log("note", "%s /kv/%s/%s" % (how, ns, fp))

if "--checkin" in sys.argv:
    text = fk.checkin_text(fp, ns)
    nonce = str(int(time.time() * 1000))  # 毫秒时钟，天然严格递增
    sig = fk.sig_b64(fk.sign(("%s|%s|%s" % (ROOM, nonce, text)).encode(), seed))
    code, body = flopnote.get("%s/r/%s/say-signed/%s/%s/%s/%s" % (
        flopnote.BASE, ROOM, urllib.parse.quote(did, safe=":"), sig, nonce,
        urllib.parse.quote(text, safe="")))
    if code != 200:
        log("say", "被拒 HTTP %d: %s" % (code, body.strip()[:200]))
        sys.exit(1)
    log("say", body.strip().splitlines()[0])
