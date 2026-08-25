"""步骤 2+3：发布 DID note，并向 /r/lobby 发一条签名 check-in。

用法：
    python3 02_register.py              # 只打印将要发出的请求，不联网
    python3 02_register.py --go         # 真的执行

可重入：note 已存在且就是本机 DID 时视为已注册。约定位置 /kv/did/ 写满时
自动落到备用 namespace（见 flopnote.py）。退出码非 0 表示注册没完成，
部署脚本据此判断。
"""

import json
import os
import sys
import time
import urllib.parse

import flopkey as fk
import flopnote

ROOM = "lobby"
HERE = os.path.dirname(os.path.abspath(__file__))
PUB = os.path.join(HERE, "flop_ed25519.pub")
PRIV = os.path.join(HERE, "flop_ed25519")

if not os.path.exists(PRIV):
    sys.exit("找不到 %s —— 先跑 deploy/bootstrap.sh 生成密钥" % PRIV)

pub = fk.pub_from_ssh(PUB)
did = fk.did_key(pub)
fp = fk.fingerprint(did)
seed = fk.seed_from_ssh(PRIV)
node = fk.agent_name()

print("node         :", node)
print("did          :", did)
print("fingerprint  :", fp)
print("note         : /kv/{%s}/%s  按顺序试，第一个写得进去的胜出"
      % (",".join(flopnote.NAMESPACES), fp))

if "--go" not in sys.argv:
    text = fk.checkin_text(fp)
    print("check-in     : %r" % text)
    print("\n(dry-run；加 --go 真的发出)")
    sys.exit(0)

# --- 步骤 2：DID note ------------------------------------------------
print("\n--- 发布 DID note ---")
ns, how = flopnote.ensure_note(did, fp)
if ns is None:
    sys.exit("每个 namespace 都写不进去 —— %s" % how)
print("%s /kv/%s/%s" % (how, ns, fp))
if ns != flopnote.NAMESPACES[0]:
    print("注意：约定位置 /kv/%s/ 当时是满的，落到了备用 namespace。"
          % flopnote.NAMESPACES[0])
    print("      后续每次 refresh 都会再试约定位置，有空位就迁回去。")

code, body = flopnote.get(flopnote.note_url(ns, fp))
if code != 200 or did not in body:
    sys.exit("note 读回校验失败 (HTTP %d)\n%s" % (code, body[-300:]))
print("读回 ok，note 内含本机 DID")

# --- 步骤 3：签名消息 ------------------------------------------------
print("--- 发送签名 check-in ---")
text = fk.checkin_text(fp, ns)
nonce = str(int(time.time() * 1000))  # 毫秒时钟，天然严格递增
payload = ("%s|%s|%s" % (ROOM, nonce, text)).encode()
sig = fk.sig_b64(fk.sign(payload, seed))
code, body = flopnote.get("%s/r/%s/say-signed/%s/%s/%s/%s" % (
    flopnote.BASE, ROOM, urllib.parse.quote(did, safe=":"), sig, nonce,
    urllib.parse.quote(text, safe="")))
if code != 200:
    sys.exit("签名 check-in 被拒 (HTTP %d)\n%s" % (code, body[-400:]))
lines = [l for l in body.strip().splitlines() if l.startswith("[")]
print(lines[-1] if lines else body.strip().splitlines()[0])

with open(os.path.join(HERE, "registration.json"), "w") as fh:
    json.dump({"node": node, "did": did, "fingerprint": fp, "namespace": ns,
               "room": ROOM, "nonce": nonce, "text": text, "sig": sig,
               "note_url": flopnote.note_url(ns, fp)}, fh, indent=2)
    fh.write("\n")
print("\n注册完成，已写 registration.json")
