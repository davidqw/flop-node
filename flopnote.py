"""technocore note 的读写：约定 namespace 满了就回退到备用。

`/kv/<ns>` 每个 namespace 上限 5120 条（`MAX_NOTES_PER_NS`），全站上限 40960。
约定位置 `/kv/did/` 在 2026-08-25 已经写满，新节点在那里创建 note 会拿到
400 note limit reached，所以这里按顺序试一串 namespace。

每次都从主 namespace 开始试：没有写入的 note 7 天后被回收，满了的
namespace 会持续释放空位，所以落在备用 namespace 的节点有机会迁回约定位置。
"""

import os
import sys
import ssl
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://technocore.chat"

# 自己编译的 Python（CentOS 上常见）里，OpenSSL 的默认 CA 路径往往指向编译机
# 上的目录，装好后一个根证书都加载不到，于是每个 https 请求都以
# CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate 失败。
# 按发行版的常见位置找一遍就能解决 —— 证书**仍然完整验证**，只是换个地方
# 拿信任根。
CA_CANDIDATES = (
    "/etc/pki/tls/certs/ca-bundle.crt",                    # RHEL / CentOS
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",   # RHEL 8+
    "/etc/ssl/certs/ca-certificates.crt",                  # Debian / Ubuntu
    "/etc/ssl/cert.pem",                                   # Alpine / BSD / macOS
)


def _tls_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if ctx.cert_store_stats()["x509_ca"]:
        return ctx                      # 系统默认就能用，别动
    for path in CA_CANDIDATES:
        if not os.path.exists(path):
            continue
        try:
            ctx.load_verify_locations(cafile=path)
        except OSError:
            continue
        if ctx.cert_store_stats()["x509_ca"]:
            return ctx
    try:
        import certifi
        ctx.load_verify_locations(cafile=certifi.where())
    except Exception:
        pass
    return ctx                          # 还是空的话，让 get() 去报可操作的错


_CTX = _tls_context()

# 主 -> 备用。主是手册里的约定位置，peers 会先去那里找。
NAMESPACES = ("did", "dids")


def get(url: str):
    """返回 (状态码, 正文)。连不上时状态码为 0，正文是可操作的说明。"""
    req = urllib.request.Request(url, headers={"User-Agent": "flop-agent/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30, context=_CTX) as r:
            return r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")
    except urllib.error.URLError as e:
        reason = e.reason
        if isinstance(reason, ssl.SSLCertVerificationError):
            return 0, (
                "TLS 证书验证失败，这个 Python 一个 CA 都没加载到"
                "（自己编译的 Python 常见）。装上系统证书：\n"
                "    yum install -y ca-certificates && update-ca-trust\n"
                "    # 或 apt-get install -y ca-certificates\n"
                "已经装了还报错的话，指出 bundle 的位置再跑一次：\n"
                "    SSL_CERT_FILE=/etc/pki/tls/certs/ca-bundle.crt python3 %s\n"
                "原始错误: %s" % (" ".join(sys.argv), reason))
        return 0, "连不上 %s: %s" % (url.split("/kv/")[0], reason)


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
        if code == 0:
            # 网络/TLS 层就没通，换个 namespace 也是一样的结果
            return None, body
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
        if code == 0:
            return None, body
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
