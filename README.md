# netease-sync — 网易云歌单每日增量备份

[![platform](https://img.shields.io/badge/platform-fnOS_|_NAS-green)]() [![runtime](https://img.shields.io/badge/runtime-python3_+_node-blue)]() [![mode](https://img.shields.io/badge/运行方式-无人值守-success)]()

> 跑在 NAS（飞牛 OS / fnOS）上的网易云歌单备份工具：每天定时把账号下的歌单镜像成本地 mp3 文件，**只补缺失的曲目**，车机 / HiFi 播放器 / navidrome 里直接离线听。

---

## 它做什么

1. **拉歌单** — 拿账号下所有歌单（含"我喜欢的音乐"）的完整曲目和添加时间
2. **对目录** — 歌单名映射到本地目录（`config.json` 的 `dir_overrides` → 精确匹配 → 宽松匹配）；没有对应目录的歌单默认跳过，不擅自建目录
3. **找缺口** — 本地已有同名 mp3（全角/半角、空格、大小写不敏感）或 manifest 记过的算已有，其余进待下载队列
4. **下载** — 接口拿直链流式写盘，ffmpeg 灌 ID3 标签（标题/艺人/专辑）并内嵌封面，先写 `.part` 再原子改名
5. **排编号** — 按加入歌单时间从近到远排 `001`、`002`…（收藏歌单的 `at` 是发行时间，自动退回接口顺序）
6. **转 NCM** — 历史上第三方客户端下的 `.ncm` 自动解密成 320 kbps mp3 并写回元数据，原件归档到 `.ncm_done/` 不删除

整个流程**无人值守、断点安全、对已有文件零打扰**：宽松匹配保证库里已有的歌不会被重复下载或改名覆盖；代码中不存在任何一条批量删除调用，临时文件只清理自己本次写入的部分。

## 三步用起来

```bash
node login.mjs              # 1. 出二维码 → 打开 扫码登录.png，手机网易云 App 扫一扫确认
python3 sync.py --dry-run   # 2. 先看差异清单，不下载
python3 sync.py             # 3. 真下载 + 重排编号
bash install-cron.sh        # 4. 装每天 05:30 自动跑
```

二维码写在 `扫码登录.png`（同时复制一份到 `state/login-qr.png`）。渲染后先用 jsQR 解码自检，解不出 codekey 就不落盘，所以这张图始终是可扫的有效码。登录成功后 Cookie 落在 `state/cookies.json`（0600），有效期通常几个月，失效时 `sync.py` 会提示重跑 `node login.mjs`。

## 常用命令

| 目的 | 命令 |
|---|---|
| 只看差异 | `python3 sync.py --dry-run` |
| 只同步某个目录 | `python3 sync.py --dir "汉斯季默精选"` |
| 只同步某个歌单 id | `python3 sync.py --playlist-id 109748081` |
| 每轮最多下 20 首（防跑飞） | `python3 sync.py --max-new 20` |
| 只做 NCM 转换 | `python3 sync.py --ncm-only` |
| 看最近一次结果 | `cat state/last-report.json` |
| 看运行日志 | `tail -f logs/cron.log` |

## 编号与重排

- `config.json` 里 `"renumber": "always"`（默认）：每次把整个目录按最新顺序重排编号，新歌自然落在最前面
- `"renumber": "off"`：老文件编号一律不动，新歌顺着最大编号往后接
- 重排是两阶段改名（先全部挪到 `.staging_*`，再落到目标名），中途不会互相覆盖；不在歌单里的文件（你自己丢进去的）原样保留

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

| 文件 | 说明 |
|---|---|
| `state/cookies.json` | 登录 Cookie（0600，别外传） |
| `state/manifest.json` | 歌单 id → {歌曲 id: 文件名}，用来认领改名后的文件 |
| `state/unavailable.json` | 无版权/取不到地址的曲子，7 天内不重试 |
| `state/last-report.json` | 最近一轮的完整结果 |

## 排错

| 症状 | 原因与处理 |
|---|---|
| `无可用播放地址 code=-110 fee=1` | 会员/版权曲目当前账号取不到；记进 `unavailable.json`，一周后再试 |
| `未登录：先跑 node login.mjs` | Cookie 过期或被顶掉，重跑 `node login.mjs` 扫码 |
| 二维码扫了没反应 | `state/login-qr.png` 每次重跑都会刷新；确认用手机网易云 App「扫一扫」，不是微信 |

## 测试

```bash
python3 tests/test_ncm.py     # NCM 往返：合成 → 解密 → 转码 → 校验标签/封面
python3 tests/test_sync.py    # 删除铁律的回归测试
```

## ⚠️ 2026-11-01 事故与铁律

用"文件名不以 NNN_ 开头就删"的规则清理残留时，把整个音乐库 1457 个文件删了。根因是把"命名规整"当成"垃圾识别"。

铁律（任何后续改动都必须守住）：

1. **脚本里不许出现基于文件名的批量删除**。要清理只清理自己这次创建的路径，并且校验路径的父目录就是本次的临时目录。
2. 目录里不认识的文件 = 用户的文件，一律保留。
3. 改名不要先 `.staging` 再落地：目标名不存在时直接 rename，只有撞名才暂存；任何一步失败必须把文件放回原名。
4. 文件名按 UTF-8 字节数截断到 240（ext4 上限 255 字节）。
5. 动真库之前先跑 `python3 rename-backup.py` 留快照。
6. 自动化先 `--dry-run`，确认差异清单再放 cron。

## English

netease-sync is a NAS-resident backup tool for NetEase Cloud Music playlists. Every night it mirrors your playlists to real mp3 files on disk — downloading only missing tracks, writing full ID3 tags with embedded cover art, and numbering files `001, 002, …` by "added at" time so the newest songs always sort first. It also decrypts legacy `.ncm` files to 320 kbps mp3 in place. Unattended, crash-safe, and never touches files it did not create.

## License

见 [LICENSE](LICENSE)。