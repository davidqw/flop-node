#!/usr/bin/env bash
# 在一台 Ubuntu 机器上安装 FLOP/technocore 节点：
# 生成本机专属密钥 -> 自检签名 -> 注册 DID -> 装每日 systemd timer。
#
# 幂等：密钥已存在就绝不覆盖，注册已存在就跳过，timer 重复装无副作用。
#
#   ./bootstrap.sh                 # 机器名自动取 hostname
#   ./bootstrap.sh --name web-01   # 显式指定节点名
#   ./bootstrap.sh --timer user    # 强制用 user timer（默认 auto）
#   ./bootstrap.sh --check         # 只做前置检查和签名自检，不生成密钥/不注册
#   ./bootstrap.sh --adopt-key     # 沿用一把别处生成的密钥（默认会拒绝，见下）
set -euo pipefail

# 默认就是这个仓库自己的根目录（脚本在 deploy/ 下），所以 git clone 到哪都能跑
FLOP_DIR="${FLOP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
NODE_NAME=""
TIMER_MODE="auto"
CHECK_ONLY=0
ADOPT_KEY=0

while [ $# -gt 0 ]; do
    case "$1" in
        --name)  NODE_NAME="$2"; shift 2 ;;
        --timer) TIMER_MODE="$2"; shift 2 ;;
        --dir)   FLOP_DIR="$2"; shift 2 ;;
        --check) CHECK_ONLY=1; shift ;;
        --adopt-key) ADOPT_KEY=1; shift ;;
        *) echo "未知参数: $1" >&2; exit 2 ;;
    esac
done

say() { printf '\n== %s\n' "$*"; }

# ---------------------------------------------------------------- 前置检查
say "前置检查"
command -v python3 >/dev/null || { echo "缺 python3: apt install -y python3" >&2; exit 1; }
command -v ssh-keygen >/dev/null || { echo "缺 ssh-keygen: apt install -y openssh-client" >&2; exit 1; }
python3 -c 'import urllib.request' || { echo "python3 标准库不完整" >&2; exit 1; }
echo "python3: $(python3 --version)"

[ -d "$FLOP_DIR" ] || { echo "$FLOP_DIR 不存在 —— 先把代码同步过来（见 push.sh）" >&2; exit 1; }
cd "$FLOP_DIR"
for f in flopkey.py 02_register.py 03_refresh.py test_sign.py; do
    [ -f "$f" ] || { echo "缺文件: $FLOP_DIR/$f" >&2; exit 1; }
done

# 节点名：显式 > hostname。写进 env 文件，让 timer 和手工执行看到同一个值。
if [ -z "$NODE_NAME" ]; then
    NODE_NAME="$(hostname -s | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9_-' '-' | sed 's/-\+/-/g; s/^-//; s/-$//')"
fi
echo "节点名: $NODE_NAME"
export FLOP_AGENT_NAME="$NODE_NAME"

# ---------------------------------------------------------------- 自检
# 部署机器多半没有 cryptography，签名会走纯 Python 回退。先证明它对，
# 否则后面 note 写得再勤，签名验不过也是白搭。
say "签名自检"
python3 test_sign.py

if [ "$CHECK_ONLY" = 1 ]; then
    say "--check 通过：这台机器可以部署（未生成密钥、未注册、未装 timer）"
    exit 0
fi

# timer 和手工执行要看到同一个节点名
printf 'FLOP_AGENT_NAME=%s\n' "$NODE_NAME" > "$FLOP_DIR/node.env"

# ---------------------------------------------------------------- 密钥
say "本机密钥"
if [ -f "$FLOP_DIR/flop_ed25519" ]; then
    # 密钥的 comment 记着是哪台机器生成的。对不上就是被复制过来的——
    # 多台机器共用一把密钥就是共用一个身份：note 互相覆盖，lobby 里会看到
    # 同一个 DID 顶着不同节点名发言，等于白装一台。
    KEY_OWNER="$(awk '{print $3}' "$FLOP_DIR/flop_ed25519.pub" 2>/dev/null || true)"
    if [ "$KEY_OWNER" != "flop-node-$NODE_NAME" ] && [ "$ADOPT_KEY" != 1 ]; then
        cat >&2 <<MSG
!! 这把密钥不是本机生成的
   密钥标记: ${KEY_OWNER:-（无）}
   本机应为: flop-node-$NODE_NAME

   多台机器共用一把密钥 = 共用一个 DID，装再多台也只是一个身份。
   最常见的原因是把整个目录（含私钥）复制了过来 —— 同步代码请用
   deploy/push.sh，或 rsync 时排除 flop_ed25519*。

   让这台机器拿到自己的身份：
     rm -f $FLOP_DIR/flop_ed25519 $FLOP_DIR/flop_ed25519.pub $FLOP_DIR/registration.json
     ./deploy/bootstrap.sh${NODE_NAME:+ --name $NODE_NAME}

   确实想沿用这把密钥（例如有意把某台机器的身份迁移过来），加 --adopt-key。
MSG
        exit 1
    fi
    echo "已存在，保持不动（覆盖会永久丢失这台机器的身份）"
    if [ "$ADOPT_KEY" = 1 ] && [ "$KEY_OWNER" != "flop-node-$NODE_NAME" ]; then
        echo "   注意：--adopt-key，沿用 ${KEY_OWNER:-（无标记）} 生成的密钥"
    fi
else
    ssh-keygen -t ed25519 -f "$FLOP_DIR/flop_ed25519" -N "" -C "flop-node-$NODE_NAME" -q
    echo "已生成"
fi
chmod 600 "$FLOP_DIR/flop_ed25519"
chmod 700 "$FLOP_DIR"

# ---------------------------------------------------------------- 注册
say "注册 DID"
python3 02_register.py --go

# ---------------------------------------------------------------- 定时器
say "每日 timer"
PY="$(command -v python3)"
UNIT_BODY_SERVICE="[Unit]
Description=FLOP/technocore DID note refresh ($NODE_NAME)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$FLOP_DIR
EnvironmentFile=$FLOP_DIR/node.env
ExecStart=$PY $FLOP_DIR/03_refresh.py --checkin"

# OnCalendar=daily 是 00:00；RandomizedDelaySec 把各机器散开到一天里的随机
# 时刻，既不在同一秒打服务器，也不会让多台机器看起来是同一个批处理。
# Persistent=true：关机错过的那次，开机后补跑。
UNIT_BODY_TIMER="[Unit]
Description=Daily FLOP DID note refresh ($NODE_NAME)

[Timer]
OnCalendar=daily
RandomizedDelaySec=6h
Persistent=true

[Install]
WantedBy=timers.target"

install_system_timer() {
    printf '%s\nUser=%s\nGroup=%s\n' "$UNIT_BODY_SERVICE" "$(id -un)" "$(id -gn)" \
        | $SUDO tee /etc/systemd/system/flop-refresh.service >/dev/null
    printf '%s\n' "$UNIT_BODY_TIMER" \
        | $SUDO tee /etc/systemd/system/flop-refresh.timer >/dev/null
    $SUDO systemctl daemon-reload
    $SUDO systemctl enable --now flop-refresh.timer
    $SUDO systemctl list-timers flop-refresh.timer --no-pager
    echo "日志: journalctl -u flop-refresh.service"
}

install_user_timer() {
    mkdir -p "$HOME/.config/systemd/user"
    printf '%s\n' "$UNIT_BODY_SERVICE" > "$HOME/.config/systemd/user/flop-refresh.service"
    printf '%s\n' "$UNIT_BODY_TIMER"   > "$HOME/.config/systemd/user/flop-refresh.timer"
    systemctl --user daemon-reload
    systemctl --user enable --now flop-refresh.timer
    systemctl --user list-timers flop-refresh.timer --no-pager || true
    # 没开 linger 的话，用户一登出 user manager 就停了，timer 也就不跑了。
    if command -v loginctl >/dev/null && \
       [ "$(loginctl show-user "$(id -un)" -p Linger --value 2>/dev/null || echo no)" != "yes" ]; then
        echo "!! 未开启 linger：登出后 timer 不会运行。执行："
        echo "   sudo loginctl enable-linger $(id -un)"
    fi
    echo "日志: journalctl --user -u flop-refresh.service"
}

SUDO=""
if [ "$(id -u)" = 0 ]; then
    SUDO=""
elif sudo -n true 2>/dev/null; then
    SUDO="sudo"
fi

case "$TIMER_MODE" in
    system) [ "$(id -u)" = 0 ] || [ -n "$SUDO" ] || { echo "system timer 需要 root/免密 sudo" >&2; exit 1; }
            install_system_timer ;;
    user)   install_user_timer ;;
    none)   echo "按要求跳过 timer 安装" ;;
    auto)   if [ "$(id -u)" = 0 ] || [ -n "$SUDO" ]; then install_system_timer; else install_user_timer; fi ;;
    *) echo "--timer 只能是 system|user|none|auto" >&2; exit 2 ;;
esac

say "完成"
python3 -c "import json;r=json.load(open('registration.json'));print('节点 :',r['node']);print('DID  :',r['did']);print('note :',r['note_url'])"
echo
echo "私钥在 $FLOP_DIR/flop_ed25519 —— 这台机器的身份就是它，先备份再说。"
