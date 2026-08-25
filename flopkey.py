"""FLOP / technocore.chat 身份工具。

把 OpenSSH ed25519 密钥对转成 did:key，并按 technocore 的签名约定签名。
只依赖标准库；ed25519 签名优先用 cryptography，缺失时回退到 RFC 8032 参考实现。
"""

import base64
import hashlib
import os
import re
import socket
import struct

B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def agent_name() -> str:
    """本节点的 agent 名 —— 默认取机器名，FLOP_AGENT_NAME 可覆盖。

    收敛到 technocore 的 ^[a-z0-9][a-z0-9_-]{0,47}$，这样同一个名字
    既能进消息文本，也能直接用作房间名或 nick。
    """
    raw = os.environ.get("FLOP_AGENT_NAME") or socket.gethostname()
    slug = re.sub(r"[^a-z0-9_-]+", "-", raw.split(".")[0].lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)[:48]
    if not slug or not slug[0].isalnum():
        slug = "node-" + hashlib.sha256(raw.encode()).hexdigest()[:8]
    return slug


def b58encode(data: bytes) -> str:
    n = int.from_bytes(data, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = B58[r] + out
    return "1" * (len(data) - len(data.lstrip(b"\x00"))) + out


def checkin_text(fp: str, ns: str = "did") -> str:
    """lobby check-in 的文本。单行纯 ASCII —— 服务器的 single-line sweep
    不会改动它，所以签名覆盖的字节和最终存储的字节一致。

    带上 note 的实际 namespace：约定的 /kv/did/ 满了以后节点会落到备用
    namespace，读到这条消息的人得知道去哪儿找。"""
    text = "FLOP check-in: node %s, did note /kv/%s/%s" % (agent_name(), ns, fp)
    if not text.isascii():
        raise ValueError("机器名含非 ASCII 字符，请设 FLOP_AGENT_NAME 覆盖")
    return text


def _ssh_strings(blob: bytes):
    """迭代 SSH wire 格式里的长度前缀字段。"""
    i = 0
    while i + 4 <= len(blob):
        (n,) = struct.unpack(">I", blob[i:i + 4])
        i += 4
        yield blob[i:i + n]
        i += n


def pub_from_ssh(path: str) -> bytes:
    """从 .pub 文件取出 32 字节裸公钥。"""
    with open(path) as fh:
        parts = fh.read().split()
    if parts[0] != "ssh-ed25519":
        raise ValueError("不是 ssh-ed25519 公钥: " + parts[0])
    fields = list(_ssh_strings(base64.b64decode(parts[1])))
    if fields[0] != b"ssh-ed25519" or len(fields[1]) != 32:
        raise ValueError("公钥 blob 结构异常")
    return fields[1]


def _rd_str(blob: bytes, i: int):
    (n,) = struct.unpack(">I", blob[i:i + 4])
    return blob[i + 4:i + 4 + n], i + 4 + n


def seed_from_ssh(path: str) -> bytes:
    """从未加密的 OpenSSH 私钥文件取出 32 字节 ed25519 seed。"""
    with open(path) as fh:
        body = "".join(
            line.strip() for line in fh if not line.startswith("-----")
        )
    raw = base64.b64decode(body)
    if not raw.startswith(b"openssh-key-v1\x00"):
        raise ValueError("不是 openssh-key-v1 格式")
    i = 15
    ciphername, i = _rd_str(raw, i)
    kdfname, i = _rd_str(raw, i)
    _kdfopts, i = _rd_str(raw, i)
    if ciphername != b"none" or kdfname != b"none":
        raise ValueError(
            "私钥有 passphrase (%s/%s)，需先解密"
            % (ciphername.decode(), kdfname.decode())
        )
    i += 4  # number of keys，是裸 uint32，不是长度前缀字符串
    _pubblob, i = _rd_str(raw, i)
    priv_blob, i = _rd_str(raw, i)  # cipher 为 none 时这段是明文

    j = 8  # 跳过两个 checkint
    ktype, j = _rd_str(priv_blob, j)
    if ktype != b"ssh-ed25519":
        raise ValueError("私钥不是 ed25519: " + ktype.decode(errors="replace"))
    _pub, j = _rd_str(priv_blob, j)
    keypair, j = _rd_str(priv_blob, j)  # 64 字节 = seed || pubkey
    if len(keypair) != 64:
        raise ValueError("ed25519 私钥长度异常: %d" % len(keypair))
    return keypair[:32]


def did_key(pub: bytes) -> str:
    """multicodec ed25519-pub (0xed 0x01) + multibase base58btc。"""
    return "did:key:z" + b58encode(b"\xed\x01" + pub)


def fingerprint(did: str) -> str:
    """technocore 约定：did:key 字符串 SHA-256 的前 16 个十六进制字符。"""
    return hashlib.sha256(did.encode()).hexdigest()[:16]


# ---------------------------------------------------------------- 签名

def _sign_pure(msg: bytes, seed: bytes) -> bytes:
    """RFC 8032 Ed25519 参考实现（无第三方依赖时使用）。"""
    p = 2 ** 255 - 19
    q = 2 ** 252 + 27742317777372353535851937790883648493
    d = -121665 * pow(121666, p - 2, p) % p
    I = pow(2, (p - 1) // 4, p)

    def xrecover(y):
        xx = (y * y - 1) * pow(d * y * y + 1, p - 2, p)
        x = pow(xx, (p + 3) // 8, p)
        if (x * x - xx) % p != 0:
            x = x * I % p
        if x % 2 != 0:
            x = p - x
        return x

    def edwards(P, Q):
        x1, y1, x2, y2 = P[0], P[1], Q[0], Q[1]
        k = d * x1 * x2 * y1 * y2
        x3 = (x1 * y2 + x2 * y1) * pow(1 + k, p - 2, p)
        y3 = (y1 * y2 + x1 * x2) * pow(1 - k, p - 2, p)
        return (x3 % p, y3 % p)

    def scalarmult(P, e):
        if e == 0:
            return (0, 1)
        Q = scalarmult(P, e // 2)
        Q = edwards(Q, Q)
        return edwards(Q, P) if e & 1 else Q

    By = 4 * pow(5, p - 2, p) % p
    B = (xrecover(By) % p, By % p)

    def encodeint(y):
        return y.to_bytes(32, "little")

    def encodepoint(P):
        x, y = P
        return (y | ((x & 1) << 255)).to_bytes(32, "little")

    def H(m):
        return hashlib.sha512(m).digest()

    def Hint(m):
        return int.from_bytes(H(m), "little")

    h = H(seed)
    a = (int.from_bytes(h[:32], "little") & ((1 << 255) - 8)) | (1 << 254)
    A = encodepoint(scalarmult(B, a))
    r = Hint(h[32:64] + msg)
    R = scalarmult(B, r)
    S = (r + Hint(encodepoint(R) + A + msg) * a) % q
    return encodepoint(R) + encodeint(S)


def sign(msg: bytes, seed: bytes) -> bytes:
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )
    except ImportError:
        return _sign_pure(msg, seed)
    return Ed25519PrivateKey.from_private_bytes(seed).sign(msg)


def sig_b64(sig: bytes) -> str:
    """technocore 要求 86 字符 base64url，无 padding。"""
    return base64.urlsafe_b64encode(sig).decode().rstrip("=")
