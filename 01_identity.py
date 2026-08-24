"""步骤 1：从现有 SSH ed25519 密钥推导 did:key，并自检签名链路。"""

import base64
import json
import os

import flopkey as fk

PUB = os.path.expanduser("./flop_ed25519.pub")
PRIV = os.path.expanduser("./flop_ed25519")

pub = fk.pub_from_ssh(PUB)
did = fk.did_key(pub)
fp = fk.fingerprint(did)

print("pubkey(hex)  :", pub.hex())
print("did:key      :", did)
print("fingerprint  :", fp)
print("note url     : /kv/did/%s" % fp)

# 私钥自检：seed 推导出的公钥必须和 .pub 一致，签名必须能被公钥验证。
seed = fk.seed_from_ssh(PRIV)
probe = fk.sign(b"technocore-selftest", seed)
try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    Ed25519PublicKey.from_public_bytes(pub).verify(probe, b"technocore-selftest")
    backend = "cryptography"
except ImportError:
    backend = "pure-python (RFC 8032)"
    assert fk._sign_pure(b"technocore-selftest", seed) == probe

print("sign backend :", backend)
print("selftest     : ok, %d 字节签名 -> %d 字符 base64url"
      % (len(probe), len(fk.sig_b64(probe))))

with open(os.path.join(os.path.dirname(__file__), "identity.json"), "w") as fh:
    json.dump({"did": did, "fingerprint": fp,
               "pubkey_b64url": base64.urlsafe_b64encode(pub).decode().rstrip("="),
               "source_key": PUB}, fh, indent=2)
    fh.write("\n")
print("written      : identity.json (只含公开信息)")
