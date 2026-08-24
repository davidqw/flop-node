#!/usr/bin/env bash
# 把代码推到多台 Ubuntu 机器并在每台上跑 bootstrap.sh。
#
# 私钥永远不经过这里：每台机器在 bootstrap 里生成自己的 flop_ed25519，
# 所以每台是一个独立身份，一台被入侵不影响其它台。
#
#   ./push.sh nodes.txt                        # 推送 + 部署
#   ./push.sh nodes.txt --dry-run              # 只看会做什么
#   ./push.sh nodes.txt --refresh              # 只同步代码，不重跑 bootstrap
#   ./push.sh nodes.txt --key ~/.ssh/server_key   # 全部机器用这把私钥
#   ./push.sh nodes.txt --password             # 密码登录（每台只提示一次）
#   ./push.sh nodes.txt --install-key --key ~/.ssh/server_key
#                                              # 把公钥装到各机器，之后免密
#
# nodes.txt 每行一台：  <ssh目标> [节点名] [ssh私钥]
#   ubuntu@10.0.0.11    web-01              ~/.ssh/server_key
#   root@example.com    -                   ~/.ssh/id_ed25519
#   ubuntu@10.0.0.12    web-02
# 第三列优先于 --key；两个都没有就走 ssh 默认（~/.ssh/config 等）。
# 节点名想留空又要写密钥时，第二列填 - 占位。
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NODES="${1:-}"
shift || true
DRY=0; ONLY_SYNC=0; PASSWORD=0; INSTALL_KEY=0; KEY="${FLOP_SSH_KEY:-}"
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run)     DRY=1; shift ;;
        --refresh)     ONLY_SYNC=1; shift ;;
        --key)         KEY="$2"; shift 2 ;;
        --password)    PASSWORD=1; shift ;;
        --install-key) INSTALL_KEY=1; shift ;;
        *) echo "未知参数: $1" >&2; exit 2 ;;
    esac
done

[ -n "$NODES" ] && [ -f "$NODES" ] || { echo "用法: $0 <nodes文件> [--dry-run] [--refresh]" >&2; exit 2; }
command -v rsync >/dev/null || { echo "本机缺 rsync" >&2; exit 1; }

REMOTE_DIR='$HOME/flop'

# 排除的都是「每台机器自己的东西」或密钥 —— 推过去会互相覆盖身份。
EXCLUDES=(
    --exclude 'flop_ed25519'      --exclude 'flop_ed25519.pub'
    --exclude 'registration.json' --exclude 'identity.json'
    --exclude 'node.env'          --exclude '*.log'
    --exclude '__pycache__'       --exclude '.env'
    --exclude 'key'               --exclude '.git'
)

fail=0
while read -r target name key _rest <&3; do
    case "$target" in ''|'#'*) continue ;; esac
    [ "$name" = "-" ] && name=""     # 占位符：只想指定密钥、不想改节点名
    printf '\n######## %s %s\n' "$target" "${name:+(节点名 $name)}"

    # 每行第三列 > --key/$FLOP_SSH_KEY > ssh 自己的默认
    node_key="${key:-$KEY}"
    ssh_opts=(-o ConnectTimeout=15)

    if [ "$PASSWORD" = 0 ]; then
        ssh_opts+=(-o BatchMode=yes)   # 免密场景下宁可立刻失败，也不要卡在密码提示上
    else
        # 一次密码，多次复用：第一个连接留下一个 master socket，同一台机器后面的
        # ssh 和 rsync 都搭它的车，所以不会连问三遍密码。%C 是短哈希，socket 路径
        # 有长度上限，别改成带主机名的长路径。ControlPersist 是空闲计时，rsync
        # 传多久都不会把它熬过期。
        ssh_opts+=(-o ControlMaster=auto -o "ControlPath=/tmp/flop-cm-%C"
                   -o ControlPersist=60)
        # 不指定密钥时就别让 agent 拿一堆公钥去试，直接走密码
        [ -n "$node_key" ] || ssh_opts+=(-o PubkeyAuthentication=no)
    fi

    if [ -n "$node_key" ]; then
        node_key="${node_key/#\~/$HOME}"
        if [ ! -f "$node_key" ]; then
            echo "!! 找不到私钥 $node_key"; fail=1; continue
        fi
        # IdentitiesOnly：~/.ssh 里密钥一多，agent 会挨个试，服务器常常在试到
        # 正确那把之前就以 Too many authentication failures 断开。
        ssh_opts+=(-i "$node_key" -o IdentitiesOnly=yes)
        echo "密钥: $node_key"
    fi
    # rsync 的 -e 收的是一个字符串，逐项转义，免得路径里的空格把命令拆散
    rsh="ssh"
    for o in "${ssh_opts[@]}"; do rsh="$rsh $(printf '%q' "$o")"; done

    if [ "$INSTALL_KEY" = 1 ]; then
        pub="${node_key:+$node_key.pub}"
        [ -n "$pub" ] || { echo "!! --install-key 需要 --key 或 nodes.txt 第三列指明用哪把密钥"; fail=1; continue; }
        [ -f "$pub" ] || { echo "!! 找不到公钥 $pub"; fail=1; continue; }
        if [ "$DRY" = 1 ]; then
            echo "ssh-copy-id -i $pub $target"
        else
            # 这一步会问密码（每台一次）。装好之后就不用 --password 了。
            ssh-copy-id -i "$pub" -o ConnectTimeout=15 "$target" || { echo "!! $target 装公钥失败"; fail=1; }
        fi
        continue
    fi

    if [ "$DRY" = 1 ]; then
        echo "rsync -az -e \"$rsh\" ${EXCLUDES[*]} $SRC/ $target:flop/"
        [ "$ONLY_SYNC" = 1 ] || echo "ssh ${ssh_opts[*]} $target 'cd flop && ./deploy/bootstrap.sh ${name:+--name $name}'"
        continue
    fi

    ssh "${ssh_opts[@]}" "$target" "mkdir -p ~/flop && chmod 700 ~/flop" || { echo "!! $target 连不上"; fail=1; continue; }
    rsync -az -e "$rsh" "${EXCLUDES[@]}" "$SRC/" "$target:flop/" || { echo "!! $target rsync 失败"; fail=1; continue; }

    if [ "$ONLY_SYNC" = 1 ]; then
        echo "已同步代码（未重跑 bootstrap）"
        continue
    fi

    # -t 让 sudo 在需要时能提示；bootstrap 本身是幂等的，重复跑安全。
    ssh "${ssh_opts[@]}" -t "$target" "cd ~/flop && ./deploy/bootstrap.sh ${name:+--name $name}" || { echo "!! $target bootstrap 失败"; fail=1; }
done 3< "$NODES"

printf '\n########\n'
if [ "$INSTALL_KEY" = 1 ] && [ "$fail" = 0 ]; then
    if [ "$DRY" = 1 ]; then
        echo "dry-run：以上是会执行的 ssh-copy-id，公钥并没有真的装上去"
    else
        echo "公钥已装好。之后正常跑就不用再输密码了："
        echo "  ./push.sh $NODES${KEY:+ --key $KEY}"
    fi
    exit 0
fi
[ "$fail" = 0 ] && echo "全部成功" || { echo "有机器失败，见上面的 !! 行"; exit 1; }
