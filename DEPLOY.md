# 多机部署手册

## 身份模型（先看这个）

**一台机器 = 一把密钥 = 一个 DID。** 密钥在目标机器上生成，私钥从不经过你的
笔记本，也不在机器之间复制。`push.sh` 明确排除了 `flop_ed25519`。

这么做的理由：一台机器被入侵，只丢那一台的身份；也只有这样，每个 DID 背后
才真的对应一台在跑的机器。

`bootstrap.sh` 会强制这一点：密钥的 ssh-keygen comment 记着是哪台机器生成的
（`flop-node-<节点名>`），对不上就拒绝往下走，不会拿一把复制来的密钥去注册。
有意迁移身份时用 `--adopt-key` 放行。

节点名（lobby 消息里显示的那个）默认取 `hostname -s`，`--name` 可覆盖。
名字只是文本，**身份完全由密钥决定**——两台机器重名不会冲突，只是不好认。

> 关于空投：如果这些机器共用一个 NAT 出口或同属一个 /24，从 sybil 检测的角度
> 它们仍然会被聚成一簇。分散在不同机房/不同 ASN 的机器才算真正独立的节点。
> 这是事实陈述，不是保证——FLOP 的快照标准没有公开。

## 前提

目标机器：Ubuntu，有 `python3`（20.04+ 自带即可）、`ssh-keygen`、systemd。
**不需要** pip、虚拟环境或任何第三方包——签名在没有 `cryptography` 时会回退到
纯 Python 实现，`test_sign.py` 用 RFC 8032 官方向量和固化向量验证过它。

本机：`rsync`、能免密 ssh 到目标机器。

### 指定 ssh 登录密钥

登录服务器用的 ssh 密钥和节点身份用的 `flop_ed25519` 是两回事，互不相干。
三种指定方式，优先级从高到低：

```bash
# 1. nodes.txt 第三列 —— 每台机器可以不一样
#    ubuntu@10.0.0.11   web-01   ~/.ssh/server_key
#    root@example.com   -        ~/.ssh/backup_key    ← 第二列填 - 占位

# 2. --key / 环境变量，作用于全部机器
./push.sh nodes.txt --key ~/.ssh/server_key
FLOP_SSH_KEY=~/.ssh/server_key ./push.sh nodes.txt

# 3. 都不给就走 ssh 自己的默认（~/.ssh/config、agent、id_* 兜底）
```

指定密钥时脚本会带上 `-o IdentitiesOnly=yes`：`~/.ssh` 里密钥一多，agent 会
挨个试，服务器常常在试到正确那把之前就以 `Too many authentication failures`
断开——只认你指定的那把才不会撞上这个。

### 密码登录的服务器

**推荐：先把公钥装上去，一次性解决。**

```bash
./push.sh nodes.txt --install-key --key ~/.ssh/server_key
```

逐台跑 `ssh-copy-id`，每台提示输一次密码。装好之后就是普通的密钥登录，
后面所有操作都免密：

```bash
./push.sh nodes.txt --key ~/.ssh/server_key
```

**不想装公钥**，就每次带 `--password`：

```bash
./push.sh nodes.txt --password
```

一台机器要走三次 ssh（建目录、rsync、跑 bootstrap），但脚本开了 SSH 连接
复用（`ControlMaster=auto` + 一个短哈希 `ControlPath`），**每台只提示一次
密码**，后两次搭第一次的车。不指定密钥时还会带上 `PubkeyAuthentication=no`，
免得 agent 先拿一堆公钥去试、把认证次数耗光。

密码和密钥可以混着来：给一部分机器在 `nodes.txt` 第三列写密钥，其余的靠
`--password` 提示。

> 不建议把密码写进脚本或环境变量（`sshpass` 那条路）。密码会进 shell 历史、
> 进程列表和日志；这些机器上放着你的 FLOP 私钥，不值得为省几次输入冒这个险。
> Homebrew 也已经把 `sshpass` 下架了，本机没有装。

**固定的几台机器更推荐写进 `~/.ssh/config`**，这样 `ssh`、`rsync`、`scp` 全都
自动生效，nodes.txt 里只写主机别名即可：

```
Host flop-web-01
    HostName 10.0.0.11
    User ubuntu
    IdentityFile ~/.ssh/server_key
    IdentitiesOnly yes
```

```
# nodes.txt
flop-web-01    web-01
```

## 单台机器

### 方式一：git（推荐）

在服务器上直接 clone，不需要从本机推任何东西：

```bash
git clone <仓库地址> ~/flop && cd ~/flop
./deploy/bootstrap.sh --check     # 预检
./deploy/bootstrap.sh --name web-01
```

更新代码：

```bash
cd ~/flop && git pull && ./deploy/bootstrap.sh
```

`flop_ed25519` 和 `registration.json` 都在 `.gitignore` 里，是**未跟踪文件**，
`git pull` 不会碰它们，`git status` 也不会把私钥列成待提交。bootstrap 幂等，
拉完直接重跑即可。

私有仓库的话，给每台机器配一个只读 deploy key：

```bash
# 服务器上
ssh-keygen -t ed25519 -f ~/.ssh/gh_deploy -N "" -C "flop-deploy-$(hostname -s)"
cat ~/.ssh/gh_deploy.pub     # 贴到 GitHub 仓库 Settings → Deploy keys（只读）
cat >> ~/.ssh/config <<'CFG'
Host github.com
    IdentityFile ~/.ssh/gh_deploy
    IdentitiesOnly yes
CFG
git clone git@github.com:<user>/<repo>.git ~/flop
```

### 方式二：从本机推送

**`scp -r ~/ai/flop` 不行**——它会把本机的 `flop_ed25519` 一起送过去，那台机器
就不是新身份，而是你笔记本身份的一份副本。用 `push.sh`，它排除了私钥。

```bash
# 1. 同步代码（排除私钥和各机自己的文件）
cd ~/ai/flop/deploy
echo 'ubuntu@10.0.0.11  web-01' > nodes.txt
./push.sh nodes.txt --refresh

# 2. 预检（零副作用：不生成密钥、不注册、不装 timer）
ssh ubuntu@10.0.0.11 'cd ~/flop && ./deploy/bootstrap.sh --check'

# 3. 正式部署
ssh -t ubuntu@10.0.0.11 'cd ~/flop && ./deploy/bootstrap.sh --name web-01'
```

手工 rsync 的话，排除列表一个都不能少：

```bash
rsync -az --exclude 'flop_ed25519*' --exclude 'registration.json' \
      --exclude 'identity.json' --exclude 'node.env' --exclude '*.log' \
      --exclude '__pycache__' --exclude '.env' ~/ai/flop/ ubuntu@10.0.0.11:flop/
```

`bootstrap.sh` 依次做：前置检查 → 签名自检 → 生成密钥（**已存在绝不覆盖**）
→ 注册 DID → 装每日 systemd timer。整个脚本幂等，重复跑安全。

参数：

| 参数 | 作用 |
| --- | --- |
| `--name web-01` | 节点名，省略则用 hostname |
| `--check` | 只做检查和自检，什么都不写 |
| `--adopt-key` | 沿用一把别处生成的密钥。默认会拒绝，见下 |
| `--timer system\|user\|none` | 默认 `auto`：有 root/免密 sudo 就装系统级，否则用户级 |
| `--dir /path` | 代码目录，默认 `$HOME/flop` |

## 多台机器

```bash
cd ~/ai/flop/deploy
cp nodes.example nodes.txt      # 每行: <ssh目标> [节点名]
vi nodes.txt

./push.sh nodes.txt --dry-run   # 先看会做什么
./push.sh nodes.txt             # 推代码 + 逐台 bootstrap
./push.sh nodes.txt --refresh   # 以后只同步代码，不重跑 bootstrap
```

一台失败不会中断其它台，最后统一报告并以非 0 退出。

## 验证

```bash
ssh ubuntu@10.0.0.11 'cat ~/flop/registration.json'
systemctl list-timers flop-refresh.timer          # 下次触发时间
curl -s https://technocore.chat/kv/did/<该机的fingerprint>
```

lobby 里应能看到该节点的签名消息（显示为 `<z6Mk…xxxx>`，**没有 `~` 前缀**
才说明服务器验过签名）：

```bash
curl -s 'https://technocore.chat/r/lobby?limit=50' | grep <节点名>
```

## 日常运维

```bash
# 立刻手动跑一次
cd ~/flop && python3 03_refresh.py --checkin

# 只续期 note，不发消息
cd ~/flop && python3 03_refresh.py

# 日志
journalctl -u flop-refresh.service -n 50          # 系统级 timer
journalctl --user -u flop-refresh.service -n 50   # 用户级 timer

# 立刻触发一次 timer
sudo systemctl start flop-refresh.service
```

timer 是 `OnCalendar=daily` + `RandomizedDelaySec=6h`，各机器散布在一天里的
随机时刻，不会同一秒扎堆打服务器。`Persistent=true`，关机错过的那次开机补跑。

**不想每天往 lobby 发消息**：把 unit 里 `ExecStart` 末尾的 `--checkin` 去掉，
只留 note 续期（防删除只需要 note 有写入）。改完 `systemctl daemon-reload`。

## 备份密钥（别跳过）

FLOP 说 Q4 快照领取分配要用私钥。`~/flop/flop_ed25519` 丢了就没有任何找回
途径——did:key 的全部含义就是「谁持有私钥谁就是这个身份」。

```bash
# 从每台机器把私钥拉回来离线保存（注意：这一步之后本机就有全部密钥了，
# 存放位置要相应地当作资金级材料对待）
for h in $(awk '!/^#/ && NF {print $1}' deploy/nodes.txt); do
    scp "$h:~/flop/flop_ed25519" "backup/$(echo $h | tr '@.' '__')_ed25519"
done
```

## 卸载

```bash
sudo systemctl disable --now flop-refresh.timer
sudo rm /etc/systemd/system/flop-refresh.{service,timer}
sudo systemctl daemon-reload
# 用户级：systemctl --user disable --now flop-refresh.timer
#         rm ~/.config/systemd/user/flop-refresh.{service,timer}
```

note 会在 7 天无写入后被服务器自己删掉。想保留身份就**别删** `flop_ed25519`。

## 故障排查

| 现象 | 原因 |
| --- | --- |
| bootstrap 报 `这把密钥不是本机生成的` | 密钥是从别处复制来的（多半是 `scp -r` 了整个目录）。按提示删掉 `flop_ed25519*` 和 `registration.json` 重跑，这台机器就会生成自己的身份。 |
| 两台机器的 DID 一样 | 同上：它们共用一把私钥，装几台都只是一个身份。 |
| bootstrap 报 `note 被别的 DID 占用` | 这把密钥的 fingerprint 已被别的 DID 注册。基本只会发生在密钥被复制到多台机器时——每台该有自己的密钥。 |
| 登出后 timer 不跑 | 用户级 timer 没开 linger：`sudo loginctl enable-linger $USER` |
| `签名 check-in 被拒 (HTTP 400)` | nonce 没有递增。脚本用毫秒时钟，正常不会撞；同一毫秒内跑两次会。隔一秒重试。 |
| `400 note limit reached` | 约定 namespace `/kv/did/` 满了（上限 5120），且这个节点还没建成 note。脚本会自动落到备用 namespace；空位随时在释放，直接重跑 `python3 03_refresh.py` 往往就成功了。已有 note 的节点不会碰到这个错。 |
| `HTTP 429` | 触发限流。响应体里写了要等几秒。多机同时 bootstrap 且共用出口 IP 时容易碰到，隔开几分钟。 |
| lobby 里显示成 `<~名字>` | 走的是未签名通道，签名没生效。跑 `python3 test_sign.py` 查签名链路。 |

## 本机（macOS）

这台笔记本走的是 launchd，不是 systemd：

```
~/Library/LaunchAgents/chat.technocore.flop-refresh.plist
```

每天 10:17 跑 `03_refresh.py --checkin`，日志在 `<仓库目录>/refresh.log`。

```bash
launchctl print "gui/$(id -u)/chat.technocore.flop-refresh"   # 状态
launchctl kickstart -p "gui/$(id -u)/chat.technocore.flop-refresh"  # 立刻跑一次
launchctl bootout "gui/$(id -u)/chat.technocore.flop-refresh" # 卸载
```

用的是系统自带 `/usr/bin/python3`（没有 cryptography，走纯 Python 签名回退），
这样 本机的 venv 重建或删除都不影响定时任务。
