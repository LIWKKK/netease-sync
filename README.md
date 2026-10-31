# netease-sync — 网易云歌单每日增量下载（飞牛 OS / fnOS 版）

把网易云音乐账号下的歌单同步到本地音乐库：**只补缺失的曲目**，按「加入歌单时间」
从近到远编号，自动写 ID3 标签和内嵌封面，顺手把历史遗留的 `.ncm` 解密成 mp3。

设计目标是一台 fnOS NAS 上**无人值守**跑：cron 定时、断点安全、绝不碰已有文件。

```
每天 05:30
  ├─ 读账号下所有歌单（含"我喜欢的音乐"）
  ├─ 歌单名 → 本地目录名（显式映射 / 精确 / 宽松三级匹配）
  ├─ 找缺口：本地已有（宽松名匹配或 manifest 记过）→ 跳过
  ├─ 下载：直链 → 流式写盘 → ffmpeg 灌 ID3 + 封面 → 原子改名
  ├─ 编号：按添加时间从近到远排 001、002…
  └─ NCM：目录里的 .ncm 解密 → 320k mp3 → 原件进 .ncm_done/ 归档
```

## 快速开始

```bash
# 依赖：python3.11+（pycryptodomex、segno）、ffmpeg、node 20+（playwright-core、jsqr、pngjs）
npm install
pip install pycryptodomex segno

node login.mjs              # 1. 出二维码（自动 jsQR 自检，只落可扫的码），手机网易云扫一扫
python3 sync.py --dry-run   # 2. 先看差异清单，不下载
python3 sync.py             # 3. 真下载
bash install-cron.sh        # 4. 装每天 05:30 定时（可 SCHEDULE 自定义）
```

登录态存 `state/cookies.json`（0600），有效期通常几个月，失效时 `sync.py` 会直接
提示重跑 `node login.mjs`。

## 它怎么找"缺的歌"

两级判定，宁可漏判也不重复下载：

1. `state/manifest.json` 记过（歌单 id → {歌曲 id: 文件名}）→ 已有
2. 本地目录里有同名的 mp3 —— 匹配用**宽松形式**：全角/半角、空格、大小写、
   结尾标点全部归一。库里已有的文件不管叫什么名字都不会被重下

## 编号与重排

- `renumber: "always"`（默认）：整个目录按最新顺序重排编号，新歌自然落最前
- `renumber: "off"`：老文件编号一律不动，新歌顺着最大编号往后接
- 改名策略：**目标名不冲突就直接 rename，只有撞名才两阶段暂存**；
  任何一步失败都会把文件放回原名，绝不留 `.staging_*` 孤儿
- 收藏歌单的 `at` 字段是歌曲发行时间而非添加时间，代码里已识别并退回接口顺序
  （`order: "auto"`）

## NCM 解密

不依赖固定偏移：解析 `magic | key | meta | img | audio` 布局后，对几种真实的
间隔宽度逐个尝试，用解出音频的魔数（ID3 / fLaC / MPEG sync）自证。AES-128-ECB
解 key 与元数据，RC4 变体解音频，元数据与封面写回 ID3。
`tests/test_ncm.py` 做合成往返测试（5 / 9 / 0 字节间隔三种变体全过）。

## 无头浏览器扫码登录

`login.mjs` 用 playwright-core 驱动系统 Chromium：

- 二维码截图后先用 **jsQR 解码自检**，解得出 `codekey` 才落盘——保证你打开的
  那张图永远是有效码，不会扫到残图
- 只有页面明确提示"二维码已失效"才自动换新码，平时绝不覆盖
- 拿到 `MUSIC_U` 后自动校验账号、写 0600 cookie、退出浏览器

## 安全铁律（一次真实事故换来的）

清理自己产生的临时文件时，一条"文件名不以编号开头就删"的规则把整个音乐库
1457 个文件删了。根因是把"命名规整"当成"垃圾识别"。所以现在：

1. **代码里没有一条批量删除调用**（`grep -E 'os\.remove|rmtree' sync.py` 应为空）
2. 临时下载放在目标目录的 `.dsh_tmp/`，收尾只删自己这次写入的文件
3. 目录里不认识的文件 = 用户的文件，一律保留
4. 动真库之前先跑 `python3 rename-backup.py` 留文件名快照（可 `--restore` 回滚）
5. 自动化先 `--dry-run`，确认差异清单再放 cron

`tests/test_sync.py` 是离线回归（假 Client，不碰网络不碰真库），钉住：
不重复下载、扩展名正确、新歌排 001、临时目录只清自己。

## 常用命令

| 目的 | 命令 |
|---|---|
| 只看差异 | `python3 sync.py --dry-run` |
| 只同步某个目录 | `python3 sync.py --dir "汉斯季默精选"` |
| 只同步某个歌单 id | `python3 sync.py --playlist-id 109748081` |
| 每轮最多下 N 首 | `python3 sync.py --max-new 20` |
| 只做 ncm 转换 | `python3 sync.py --ncm-only` |
| 改名快照 / 回滚 | `python3 rename-backup.py [--list / --restore FILE]` |
| 看最近一次结果 | `cat state/last-report.json` |
| 自定义定时 | `SCHEDULE="0 6 * * *" bash install-cron.sh` |

## config.json

```json
{
  "music_root": "/vol1/1000/音乐",
  "level": "exhigh",              // standard / higher / exhigh / lossless
  "renumber": "always",           // always | off
  "order": "auto",                // auto | addtime | position
  "create_missing_dirs": false,   // 歌单没有对应目录时是否自动建
  "dir_overrides": {              // 歌单名 → 本地目录名
    "我喜欢的音乐": "LiWKKKK喜欢的音乐"
  },
  "only_dirs": [],                // 非空则只处理这些目录
  "extra_playlist_ids": [],       // 额外跟踪的公开歌单
  "max_new_per_run": 0,           // 每歌单每轮上限，0 = 不限
  "ncm_scan": true,
  "ncm_archive": ".ncm_done",
  "concurrency": 4
}
```

## 目录结构

```
sync.py            主流程（下载、编号、重排、manifest）
login.mjs          无头浏览器扫码登录
lib/netease.py     API 客户端：歌单详情 / 批量详情 / 直链 / 流式下载
lib/ncm.py         NCM 解密 + ffmpeg 转码 + 打标签
lib/names.py       文件名规整（全角约定、字节级截断、宽松匹配）
run.sh             cron 入口：flock 防重入 + 日志截断
install-cron.sh    装定时任务
rename-backup.py   文件名快照与回滚
tests/             离线回归（NCM 往返 / sync 行为）
state/             运行时状态（cookies / manifest / unavailable / renames）— 不入库
```

## 已知边界

- **VIP/无版权曲目**拿不到直链（`code=-110 / 404`），记进 `state/unavailable.json`，
  7 天后自动重试；不上传、不重试到死循环
- 文件名按 UTF-8 字节数截断到 240（ext4 单名上限 255），截断保歌名尾部
- 只在 Linux + ext4/btrfs 上验证过；Windows 路径分隔符未处理

## License

MIT
