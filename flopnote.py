"""technocore note 的读写：约定 namespace 满了就回退到备用。

`/kv/<ns>` 每个 namespace 上限 5120 条（`MAX_NOTES_PER_NS`），全站上限 40960。
约定位置 `/kv/did/` 在 2026-08-25 已经写满，新节点在那里创建 note 会拿到
400 note limit reached，所以这里按顺序试一串 namespace。

每次都从主 namespace 开始试：没有写入的 note 7 天后被回收，满了的
namespace 会持续释放空位，所以落在备用 namespace 的节点有机会迁回约定位置。
"""

import urllib.error
import urllib.parse
import urllib.request

BASE = "https://technocore.chat"

# 主 -> 备用。主是手册里的约定位置，peers 会先去那里找。
NAMESPACES = ("did", "dids")


def get(url: str):
    """返回 (状态码, 正文)。"""
    req = urllib.request.Request(url, headers={"User-Agent": "flop-agent/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")


def note_url(ns: str, fp: str, base: str = BASE) -> str:
    return "%s/kv/%s/%s" % (base, ns, fp)


def ensure_note(did: str, fp: str, base: str = BASE, namespaces=NAMESPACES):
    """保证 did 在某个 namespace 下有一条属于自己的 note。

    返回 (namespace, 说明)；每个 namespace 都写不进去时返回 (None, 原因)。
    """
    value = urllib.parse.quote(did, safe=":")
    problems = []

    for ns in namespaces:
        url = note_url(ns, fp, base)

        code, body = get(url)
        if code == 200 and did in body:
            code, body = get("%s/set/%s" % (url, value))
            if code == 200:
                return ns, "renewed"
            problems.append("%s: 续期失败 HTTP %d" % (ns, code))
            continue
        if code == 200:
            # fingerprint 是 did 的 SHA-256，撞不上；能落到这里说明有人拿我们
            # 的 did 算出 fingerprint 后把这个 key 占了。换下一个 namespace。
            problems.append("%s: 被别的值占用" % ns)
            continue

        code, body = get("%s/set/%s?if_absent=1" % (url, value))
        if code == 200:
            return ns, "created"
        if code == 409:
            # 竞态：两次请求之间被写了。是自己的就当成功。
            _, existing = get(url)
            if did in existing:
                return ns, "already ours"
            problems.append("%s: 被别的值抢先" % ns)
            continue
        if code == 400 and "limit" in body:
            problems.append("%s: namespace 已满" % ns)
            continue
        problems.append("%s: HTTP %d" % (ns, code))

    return None, "；".join(problems)
