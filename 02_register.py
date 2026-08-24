"""步骤 2+3：发布 DID note，并向 /r/lobby 发一条签名 check-in。

用法：
    python3 02_register.py              # 只打印将要发出的请求，不联网
    python3 02_register.py --go         # 真的执行

可重入：note 已存在且就是本机 DID 时视为已注册。退出码非 0 表示注册没完成，
部署脚本据此判断。
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import flopkey as fk

BASE = "https://technocore.chat"
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

# 单行纯 ASCII —— 服务器的 single-line sweep 不会改动它，
# 所以签名覆盖的字节和最终存储的字节一致。
TEXT = fk.checkin_text(fp)
NONCE = str(int(time.time() * 1000))  # 毫秒时钟，天然严格递增

# --- 步骤 2：DID note ------------------------------------------------
note_url = "%s/kv/did/%s/set/%s?if_absent=1" % (
    BASE, fp, urllib.parse.quote(did, safe=":"))
read_url = "%s/kv/did/%s" % (BASE, fp)

# --- 步骤 3：签名消息 ------------------------------------------------
payload = ("%s|%s|%s" % (ROOM, NONCE, TEXT)).encode()
sig = fk.sig_b64(fk.sign(payload, seed))
say_url = "%s/r/%s/say-signed/%s/%s/%s/%s" % (
    BASE, ROOM, urllib.parse.quote(did, safe=":"), sig, NONCE,
    urllib.parse.quote(TEXT, safe=""))


def get(url: str):
    """返回 (状态码, 正文)。"""
    req = urllib.request.Request(url, headers={"User-Agent": "flop-agent/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")


print("node         :", node)
print("did          :", did)
print("fingerprint  :", fp)
print("sign payload : %r" % payload.decode())
print("sig          :", sig, "(%d chars)" % len(sig))
print()
print("[2] note ->", note_url)
print("[3] say  ->", say_url[:110] + "...")

if "--go" not in sys.argv:
    print("\n(dry-run；加 --go 真的发出)")
    sys.exit(0)

print("\n--- 发布 DID note ---")
code, body = get(note_url)
print(code, body.strip().splitlines()[-1] if body.strip() else "")
if code == 409:
    # 这个 fingerprint 已被占用。是本机之前注册的就没问题；若是别的 DID，
    # 说明同一把密钥在别处注册过或撞了，必须人工看一眼。
    _, existing = get(read_url)
    if did in existing:
        print("note 已存在且就是本机 DID —— 视为已注册")
    else:
        sys.exit("note 被别的 DID 占用，人工介入:\n" + existing[-300:])
elif code != 200:
    sys.exit("note 写入失败 (HTTP %d)" % code)

print("--- 读回 note ---")
code, body = get(read_url)
if code != 200 or did not in body:
    sys.exit("note 读回校验失败 (HTTP %d)\n%s" % (code, body[-300:]))
print("ok，note 内含本机 DID")

print("--- 发送签名 check-in ---")
code, body = get(say_url)
if code != 200:
    sys.exit("签名 check-in 被拒 (HTTP %d)\n%s" % (code, body[-400:]))
lines = [l for l in body.strip().splitlines() if l.startswith("[")]
print(lines[-1] if lines else body.strip().splitlines()[0])

with open(os.path.join(HERE, "registration.json"), "w") as fh:
    json.dump({"node": node, "did": did, "fingerprint": fp, "room": ROOM,
               "nonce": NONCE, "text": TEXT, "sig": sig,
               "note_url": read_url}, fh, indent=2)
    fh.write("\n")
print("\n注册完成，已写 registration.json")
