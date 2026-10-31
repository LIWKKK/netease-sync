# netease-sync — 网易云歌单每日增量下载

**netease-sync** 是一套跑在 NAS（飞牛 OS / fnOS）上的网易云音乐歌单备份工具。
它每天定时读取你账号下的全部歌单，与本地音乐库逐一比对，**只下载缺失的曲目**：
按网易云接口给出的直链抓取音频，用 ffmpeg 写入完整的 ID3 标签（标题、艺人、专辑）
并把专辑封面内嵌进 mp3，最后以 `NNN_艺人 - 歌名.mp3` 的形式按「加入歌单的时间
从近到远」自动编号——最新的歌永远排 001，你在文件管理器里一眼就能看到最近
收藏了什么。

它同时是一个 **NCM 解密器**：历史上用第三方客户端下载的 `.ncm` 加密文件会被
自动解密、转码成 320 kbps 的 mp3 并写回元数据，原件归档不删除。整个流程
**无人值守、断点安全、对已有文件零打扰**——比对采用全角/半角/空格/大小写
不敏感的宽松匹配，库里已有的歌无论叫什么名字都不会被重复下载或改名覆盖；
代码中不存在任何一条批量删除调用，临时文件只清理自己本次写入的部分。

适用于：想把网易云歌单持续镜像成「真实 mp3 文件」、在任意播放器（车机、
HiFi 播放器、navidrome）里离线听、并且不想手动管理的用户。

把 `/vol1/1000/音乐` 里已有的歌单目录当成目标状态，缺什么补什么。

## 三步用起来

```bash
cd netease-sync

node login.mjs              # 1. 出二维码 → 打开 扫码登录.png，手机网易云 App 扫一扫确认
python3 sync.py --dry-run   # 2. 先看差异清单，不下载
python3 sync.py             # 3. 真下载 + 重排编号
bash install-cron.sh        # 4. 装每天 05:30 自动跑
```

二维码写在 `扫码登录.png`（同时复制一份到 `state/login-qr.png`）。渲染后先用 jsQR
解码自检，解不出 codekey 就不落盘，所以这张图始终是可扫的有效码；只有页面提示
"二维码已失效"时才会自动换新码。

登录成功后 Cookie 落在 `state/cookies.json`（0600），有效期通常几个月，
失效时 `sync.py` 会直接告诉你重跑 `node login.mjs`。

## 它做什么

1. **拉歌单**：拿账号下所有歌单（含"我喜欢的音乐"）的完整曲目和添加时间。
2. **对目录**：歌单名 → 本地目录名，先查 `config.json` 的 `dir_overrides`，
   再精确匹配，再宽松匹配；没有对应目录的歌单默认跳过（不擅自建目录）。
3. **找缺口**：本地目录里已有同名 mp3（全角/半角、空格、大小写都不敏感）
   或 `state/manifest.json` 记过，就算已有；否则进待下载队列。
4. **下载**：`/api/song/enhance/player/url` 拿直链，流式写盘，再灌 ID3
   （标题/艺人/专辑/封面），先写 `.part` 再原子改名。
5. **排编号**：按"加入歌单时间"从近到远排 001、002…（收藏歌单的 `at` 是发行
   时间，这种歌单自动退回接口顺序，跟你库里现有编号习惯一致）。
6. **转 NCM**：扫目录里的 `.ncm`，解出原始音频 + 元数据 + 封面，转 320k mp3，
   原件移到 `.ncm_done/` 归档。

## 编号与重排

- `config.json` 里 `"renumber": "always"`（默认）：每次把整个目录按最新顺序
  重排编号，新歌自然落在最前面。
- `"renumber": "off"`：老文件编号一律不动，新歌顺着最大编号往后接。
- 重排是两阶段改名（先全部挪到 `.staging_*`，再落到目标名），中途不会互相覆盖；
  不在歌单里的文件（你自己丢进去的）原样保留不动。

## 常用命令

| 目的 | 命令 |
|---|---|
| 只看差异 | `python3 sync.py --dry-run` |
| 只同步某个目录 | `python3 sync.py --dir "汉斯季默精选"` |
| 只同步某个歌单 id | `python3 sync.py --playlist-id 109748081` |
| 每轮最多下 20 首（防跑飞） | `python3 sync.py --max-new 20` |
| 只做 ncm 转换 | `python3 sync.py --ncm-only` |
| 看最近一次结果 | `cat state/last-report.json` |
| 看运行日志 | `tail -f logs/cron.log` |

## config.json

```json
{
  "music_root": "/vol1/1000/音乐",
  "level": "exhigh",              // standard/higher/exhigh/lossless
  "renumber": "always",           // always | off
  "order": "auto",                // auto | addtime | position
  "create_missing_dirs": false,   // 歌单没有对应目录时是否自动建
  "dir_overrides": {"我喜欢的音乐": "LiWKKKK喜欢的音乐"},
  "only_dirs": [],                // 非空则只处理这些目录
  "extra_playlist_ids": [],       // 额外跟的（别人公开歌单也行）
  "max_new_per_run": 0,           // 每歌单每轮上限，0 = 不限
  "ncm_scan": true,
  "ncm_archive": ".ncm_done",
  "concurrency": 4
}
```

## 状态文件

- `state/cookies.json` — 登录 Cookie（0600，别外传）
- `state/manifest.json` — 歌单 id → {歌曲 id: 文件名}，用来认领改名后的文件
- `state/unavailable.json` — 无版权/取不到地址的曲子，7 天内不重试
- `state/last-report.json` — 最近一轮的完整结果

## 排错

**"无可用播放地址 code=-110 fee=1"**
会员/版权曲目，当前账号取不到。登录后仍有的话就是真没版权；脚本会记进
`unavailable.json`，一周后再试。

**"未登录：先跑 node login.mjs"**
Cookie 过期或被顶掉。重跑 `node login.mjs` 扫码即可。

**二维码扫了没反应**
`state/login-qr.png` 是浏览器实时截的图，每次重跑都会刷新；确认是拿手机
网易云 App「扫一扫」，不是微信。

## 测试

```bash
python3 tests/test_ncm.py     # NCM 往返：合成 → 解密 → 转码 → 校验标签/封面
```

## 2026-11-01 事故与铁律

用"文件名不以 NNN_ 开头就删"的规则清理残留时，把整个音乐库 1457 个文件删了。
根因是把"命名规整"当成"垃圾识别"。

铁律（任何后续改动都必须守住）：

1. **脚本里不许出现基于文件名的批量删除**。要清理只清理自己这次创建的路径，
   并且校验路径的父目录就是本次的临时目录。
2. 目录里不认识的文件 = 用户的文件，一律保留。
3. 改名不要先 .staging 再落地：目标名不存在时直接 rename，只有撞名才暂存；
   任何一步失败必须把文件放回原名。
4. 文件名按 UTF-8 字节数截断到 240（ext4 上限 255 字节）。
5. 动真库之前先跑 `python3 rename-backup.py` 留快照。
6. 自动化先 `--dry-run`，确认差异清单再放 cron。

tests/test_sync.py 是这条铁律的回归测试。