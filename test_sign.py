"""验证纯 Python ed25519 回退实现 —— 部署机器上很可能没有 cryptography，
签名一旦不对，note 写得再勤也没用。

    python3 test_sign.py

两组基准：RFC 8032 的官方向量，以及本机用 cryptography 生成、固化在下面的
向量（覆盖真实的 technocore 签名载荷）。后者让没装 cryptography 的机器也有
东西可比对。
"""

import binascii

import flopkey as fk

unhex = binascii.unhexlify

# RFC 8032 section 7.1
RFC8032 = [
    ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
     "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555f"
     "b8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
     "af82",
     "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18"
     "ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"),
]

# seed = bytes(range(32))，签名由 cryptography 47.0.0 生成
PINNED_SEED = bytes(range(32))
PINNED = [
    (b"",
     "9ca53579530654d5c3df77089ef45eda613e2fedf670e96bedac4639504e5845ef"
     "4b95d5793077233dd16817b2532e9c5525872a73a4ad74b759369a9e05c102"),
    (b"lobby|1|hi",
     "7d12f602740c9c8bd20167fc20e9508d679307910388b907d3eccb71ac3b417739"
     "89fdb4a5ebaff5cf41667267c0c1070bcc265fb80ab4f25123a8dca63bc404"),
    (b"lobby|1700000000000|FLOP check-in: node example-node, "
     b"did note /kv/did/0123456789abcdef",
     "5431b5576435bc3d99ec78eb6dd4e6507b8d71c225645c7be0a2e3941ab6891d91"
     "9320a819f1122d4dc0ceb4c29fa709989885f74f3d7a204f944f5e7c91190e"),
]

for i, (sk, msg, want) in enumerate(RFC8032, 1):
    assert fk._sign_pure(unhex(msg), unhex(sk)) == unhex(want), \
        "RFC 8032 向量 %d 不匹配" % i
    print("rfc8032 vector %d: ok" % i)

for i, (msg, want) in enumerate(PINNED, 1):
    assert fk._sign_pure(msg, PINNED_SEED) == unhex(want), \
        "固化向量 %d 不匹配" % i
    print("pinned vector %d: ok (%d 字节载荷)" % (i, len(msg)))

# 装了 cryptography 的机器上，两条实现必须逐字节一致
try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )
except ImportError:
    print("cryptography 缺失 —— 交叉比对跳过，固化向量已覆盖")
else:
    for msg, _ in PINNED:
        assert (Ed25519PrivateKey.from_private_bytes(PINNED_SEED).sign(msg)
                == fk._sign_pure(msg, PINNED_SEED))
    print("cross-check vs cryptography: ok")

# 签名编码：技术上服务器只收 86 字符无 padding base64url
assert len(fk.sig_b64(fk._sign_pure(b"x", PINNED_SEED))) == 86
print("base64url encoding: ok")
print("all ok")
