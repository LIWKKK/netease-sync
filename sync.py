#!/usr/bin/env python3
"""网易云歌单 → /vol1/1000/音乐 增量同步。

- 只补缺失的曲目，已存在（按歌名宽松匹配或 manifest 命中）一律跳过
- 编号按"加入歌单时间"从近到远（001 最新），与库内既有三位编号风格一致
- 顺手把目录里的 .ncm 解密成 mp3（含元数据与封面），原件归档
"""
import argparse, json, os, shutil, sys, time, traceback
from concurrent.futures import ThreadPoolExecutor
from urllib.request import urlopen, Request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
import netease as ne
import ncm as ncm_mod
import names as nm

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
STATE_DIR = os.path.join(HERE, "state")
MANIFEST_PATH = os.path.join(STATE_DIR, "manifest.json")
UNAVAILABLE_PATH = os.path.join(STATE_DIR, "unavailable.json")
LOG_DIR = os.path.join(HERE, "logs")
FFMPEG = shutil.which("ffmpeg") or "/usr/bin/ffmpeg"


def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(os.path.join(LOG_DIR, "sync.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def sanitize_dir(name):
    return nm.sanitize(name).replace("/", "／")


def resolve_dir(playlist_name, cfg, existing_dirs):
    """歌单名 → 本地目录名。先看显式映射，再精确匹配，再宽松匹配。"""
    overrides = cfg.get("dir_overrides") or {}
    if playlist_name in overrides:
        return overrides[playlist_name]
    # 配置里手写的键常与接口名差一个 NBSP / 全角空格，按宽松形式再对一次
    target_key = nm.norm(playlist_name)
    for key, value in overrides.items():
        if nm.norm(key) == target_key:
            return value
    cand = sanitize_dir(playlist_name)
    if cand in existing_dirs:
        return cand
    target = nm.norm(playlist_name)
    for d in existing_dirs:
        if nm.norm(d) == target:
            return d
    # 宽松：本地目录名整体包含或包含于歌单名（应对 emoji/角标差异）
    for d in existing_dirs:
        nd = nm.norm(d)
        if len(nd) >= 4 and (nd in target or target in nd):
            return d
    return None


def existing_dirs(root=None):
    root = root or load_json(CONFIG_PATH, {}).get("music_root", "/vol1/1000/音乐")
    if not os.path.isdir(root):
        return []
    return [d for d in os.listdir(root)
            if os.path.isdir(os.path.join(root, d)) and not d.startswith(".")]


def local_index(directory):
    """返回 {norm_name: filename}, {filename: index}"""
    by_name, by_file = {}, {}
    for fn in sorted(os.listdir(directory)):
        if not fn.lower().endswith(".mp3"):
            continue
        idx, rest = nm.strip_index(fn)
        by_file[fn] = idx
        by_name.setdefault(nm.norm(rest), fn)
    return by_name, by_file


def fetch_cover(url, dest):
    if not url:
        return None
    try:
        req = Request(url, headers={"User-Agent": ne.UA})
        with urlopen(req, timeout=20) as r, open(dest, "wb") as f:
            f.write(r.read())
        return dest
    except Exception:
        return None


def tag_mp3(src, dest, title, artists, album, cover_url, tmpdir):
    cover = fetch_cover(cover_url, os.path.join(tmpdir, "cover.jpg"))
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", src]
    if cover:
        cmd += ["-i", cover, "-map", "0:a", "-map", "1:v", "-c:v", "copy",
                "-disposition:v", "attached_pic"]
    cmd += ["-c:a", "copy", "-id3v2_version", "3",
            "-metadata", f"title={title}",
            "-metadata", f"artist={'/'.join(artists)}",
            "-metadata", f"album={album}", dest]
    p = __import__("subprocess").run(cmd, capture_output=True, text=True)
    if p.returncode != 0 or not os.path.exists(dest):
        if os.path.exists(src):
            shutil.move(src, dest)          # 打标签失败也要保住音频
        else:
            raise RuntimeError(f"ffmpeg 打标签失败: {p.stderr.strip()[:160]}")


UNAVAILABLE_TTL = 7 * 86400          # 无版权/取不到地址的曲目，7 天内不再重试
unavailable = {}                     # {pid:sid: 失败时间戳}，main() 里载入


def unavailable_get(pid, sid, cache):
    rec = cache.get(f"{pid}:{sid}")
    if not rec:
        return False
    return time.time() - rec < UNAVAILABLE_TTL


def unavailable_set(pid, sid, cache, reason):
    cache[f"{pid}:{sid}"] = int(time.time())
    save_json(UNAVAILABLE_PATH, cache)
    return reason


def download_track(client, track, song, directory, br, tmpdir):
    """下载 + 打标签到一个临时 mp3，返回 (tmp_path, base_name)。"""
    sid = track["id"]
    url, code, fee, real_br = client.song_url(sid, br)
    if not url:
        raise RuntimeError(f"无可用播放地址 code={code} fee={fee}（可能版权/会员限制）")
    artists = [a.get("name") for a in (song.get("ar") or song.get("artists") or []) if a.get("name")]
    title = song.get("name") or str(sid)
    album = ((song.get("al") or song.get("album") or {}).get("name")) or ""
    cover = ((song.get("al") or song.get("album") or {}).get("picUrl")) or ""
    base = nm.track_name(artists, title)
    raw = os.path.join(tmpdir, f"{sid}.raw")
    out = os.path.join(tmpdir, f"{sid}.mp3")
    client.stream(url, raw)
    tag_mp3(raw, out, title, artists, album, cover, tmpdir)
    return out, base, {"id": sid, "title": title, "artists": artists, "album": album, "br": real_br}


def convert_ncm_files(music_root, cfg, report):
    """把 music_root 下所有 .ncm 解密成同名 mp3，并归档原件。"""
    if not cfg.get("ncm_scan", True):
        return
    archive_rel = cfg.get("ncm_archive", ".ncm_done")
    converted = 0
    for root, dirs, files in os.walk(music_root):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fn in files:
            if not fn.lower().endswith(".ncm"):
                continue
            src = os.path.join(root, fn)
            stem = fn[:-4]
            stem = nm.strip_index(stem)[1] if nm.strip_index(stem)[0] else stem
            out = os.path.join(root, nm.sanitize(stem) + ".mp3")
            if os.path.exists(out):
                converted += 0  # 已有同名 mp3，只归档
            else:
                try:
                    ncm_mod.convert(src, out, "320k", FFMPEG)
                    converted += 1
                except Exception as e:
                    report["errors"].append(f"NCM {fn}: {e}")
                    continue
            archive = os.path.join(root, archive_rel)
            os.makedirs(archive, exist_ok=True)
            shutil.move(src, os.path.join(archive, fn))
    if converted:
        report["ncm_converted"] = converted


def renumber(directory, plan, dry_run, report=None):
    """plan: [(target_filename, current_filename或None, tmp_path或None)]

    目标名当前不存在时直接落盘，只有会撞名的那几个才走 .staging_ 两阶段。
    任一步失败都会把文件放回原名，绝不留下 .staging_ 孤儿。
    """
    moves = []
    for target, current, tmp_path in plan:
        if tmp_path:
            moves.append((tmp_path, os.path.join(directory, target), ""))
        elif current and current != target:
            moves.append((os.path.join(directory, current), os.path.join(directory, target), current))
    if not moves or dry_run:
        return len(moves)

    failed, direct, conflicts = [], [], []
    for src, dst, orig in moves:
        (direct if not os.path.exists(dst) else conflicts).append((src, dst, orig))

    done = 0
    for src, dst, _orig in direct:
        if not os.path.exists(src):
            continue
        try:
            os.rename(src, dst) if os.path.dirname(os.path.abspath(src)) == os.path.abspath(directory) \
                else shutil.move(src, dst)
            done += 1
        except OSError as e:
            failed.append(f"落盘 {os.path.basename(dst)[:60]}: {e}")

    staged = []
    for i, (src, dst, orig) in enumerate(conflicts):
        if not os.path.exists(src):
            continue
        tmp = os.path.join(directory, f".staging_{os.getpid()}_{i}")
        try:
            os.rename(src, tmp) if os.path.dirname(os.path.abspath(src)) == os.path.abspath(directory) \
                else shutil.move(src, tmp)
            staged.append((tmp, dst, orig))
        except OSError as e:
            failed.append(f"暂存 {os.path.basename(src)[:60]}: {e}")

    for tmp, dst, orig in staged:
        try:
            os.rename(tmp, dst)
            done += 1
        except OSError as e:
            failed.append(f"落地 {os.path.basename(dst)[:60]}: {e}")
            try:
                back = os.path.join(directory, orig) if orig else None
                if back and not os.path.exists(back):
                    os.rename(tmp, back)
            except OSError:
                pass

    if report is not None:
        report["errors"].extend(failed)
    return done

def decide_order(tids, order_mode):
    """把 trackIds 变成"从近到远"的顺序，返回 (ordered[(track, pos)], 说明)。

    addtime 只有在整张歌单都带添加时间时才可信；收藏(订阅)歌单的 at 是歌曲发行
    时间而不是添加时间，那种情况退回接口顺序（本地已有文件即按接口顺序编号）。
    """
    positions = list(enumerate(tids))
    if order_mode == "position":
        return positions, "接口顺序"
    ats = [t.get("at") or 0 for t in tids]
    if not all(ats):
        return positions, "接口顺序（部分曲目无添加时间）"
    desc = all(ats[i] >= ats[i + 1] for i in range(len(ats) - 1))
    asc = all(ats[i] <= ats[i + 1] for i in range(len(ats) - 1))
    if desc:
        return positions, "添加时间从近到远（接口已是该序）"
    if asc:
        return list(reversed(positions)), "添加时间从近到远（接口是倒序，已反转）"
    if order_mode == "addtime":
        return sorted(positions, key=lambda p: (-(p[1].get("at") or 0), p[0])), "添加时间从近到远（已重排）"
    return positions, "接口顺序（无稳定添加时间）"


def sync_playlist(client, pl, cfg, manifest, report, args):
    pid = pl["id"]
    name = pl["name"]
    dirname = resolve_dir(name, cfg, existing_dirs(cfg["music_root"]))
    status = {"id": pid, "name": name, "dir": dirname}
    if not dirname:
        if not cfg.get("create_missing_dirs"):
            report["skipped_playlists"].append(f"{name}(id={pid}) 本地没有对应目录")
            return status
        dirname = sanitize_dir(name)
        os.makedirs(os.path.join(cfg["music_root"], dirname), exist_ok=True)
    directory = os.path.join(cfg["music_root"], dirname)
    status["dir"] = dirname

    meta, tids = client.playlist_trackids(pid)
    if not tids:
        status["note"] = "空歌单"
        return status
    ids = [t["id"] for t in tids]
    songs = client.songs(ids)
    idx_by_name, idx_by_file = local_index(directory)
    pl_manifest = manifest.setdefault(str(pid), {})

    # ---- 找出缺的 ----
    missing, blocked = [], []
    for pos, t in enumerate(tids):
        sid = t["id"]
        song = songs.get(sid)
        if not song:
            continue
        artists = [a.get("name") for a in (song.get("ar") or song.get("artists") or []) if a.get("name")]
        base = nm.track_name(artists, song.get("name") or "")
        known_file = pl_manifest.get(str(sid))
        if known_file and known_file in idx_by_file:
            continue
        if nm.norm(base) in idx_by_name:
            pl_manifest[str(sid)] = idx_by_name[nm.norm(base)]      # 认领已有文件
            continue
        if unavailable_get(pid, sid, unavailable):
            blocked.append(base)
            continue
        missing.append((pos, t, song, base))

    status["total"] = len(tids)
    status["missing"] = len(missing)
    status["local"] = len(idx_by_file)
    if blocked:
        status["unavailable"] = len(blocked)
    log(f"歌单《{name}》→ {dirname}：接口 {len(tids)} 首，本地 {len(idx_by_file)} 首，"
        f"待补 {len(missing)} 首" + (f"，已知不可取 {len(blocked)} 首" if blocked else ""))

    if args.max_new and len(missing) > args.max_new:
        log(f"  本次只取最近 {args.max_new} 首（--max-new 限制）")
        missing = missing[:args.max_new]

    # ---- 下载 ----
    downloaded = {}
    if missing and not args.dry_run:
        # 临时文件直接放目标目录下的隐藏子目录：同一设备，改名是纯 rename，
        # 也不会在崩溃时把半成品留在别处。
        tmpdir = os.path.join(directory, ".dsh_tmp")
        os.makedirs(tmpdir, exist_ok=True)   # 只往里写本次下载，绝不清理别的文件
        br = ne.load_level_br(cfg.get("level", "exhigh"))

        def work(item):
            pos, t, song, base = item
            try:
                path, basename, info = download_track(client, t, song, directory, br, tmpdir)
                return ("ok", t, basename, path, info)
            except Exception as e:
                return ("fail", t, base, None, str(e))

        with ThreadPoolExecutor(max_workers=int(cfg.get("concurrency", 4))) as ex:
            for res in ex.map(work, missing):
                if res[0] == "fail":
                    report["errors"].append(f"{name}: {res[2]} → {res[4]}")
                    status.setdefault("failed", []).append(res[2])
                    if "无可用播放地址" in res[4] or "试听" in res[4]:
                        unavailable_set(pid, res[1]["id"], unavailable, res[4])
                else:
                    downloaded[res[1]["id"]] = {"base": res[2], "tmp": res[3], "info": res[4]}
                    status.setdefault("new", []).append(res[2])
                    log(f"  + {res[2]}")
        # tmpdir 里的文件要等重排落盘后才能真正删除，提前删会把新歌一起删掉
    elif missing:
        for pos, t, song, base in missing:
            status.setdefault("new", []).append(base + "（dry-run）")

    # ---- 重排编号：按加入时间从近到远 ----
    have = {}   # sid -> (base, tmp_path or None)
    for pos, t in enumerate(tids):
        sid = t["id"]
        song = songs.get(sid)
        if not song:
            continue
        if sid in downloaded:
            have[sid] = (downloaded[sid]["base"], downloaded[sid]["tmp"])
            continue
        known = pl_manifest.get(str(sid))
        if known and known in idx_by_file:
            have[sid] = (nm.strip_index(known)[1], None)
            continue
        artists = [a.get("name") for a in (song.get("ar") or song.get("artists") or []) if a.get("name")]
        base = nm.track_name(artists, song.get("name") or "")
        fn = idx_by_name.get(nm.norm(base))
        if fn:
            have[sid] = (nm.strip_index(fn)[1], None)

    sequence, order_note = decide_order(tids, cfg.get("order", "auto"))
    ordered = [(t, pos) for pos, t in sequence if t["id"] in have]
    width = 3 if len(ordered) < 1000 else len(str(len(ordered)))
    status["order"] = order_note

    renumber_mode = cfg.get("renumber", "always")   # always | off
    next_idx = max([v for v in idx_by_file.values() if v] or [0]) + 1

    # 目录里"不会被本计划腾出"的文件名，任何撞上它们的目标名都要让开
    sources = set()
    for t, _p in ordered:
        sid = t["id"]
        cur = pl_manifest.get(str(sid))
        if cur in idx_by_file:
            sources.add(cur)
        else:
            fn = idx_by_name.get(nm.norm(have[sid][0]))
            if fn:
                sources.add(fn)
    occupied = {fn for fn in os.listdir(directory) if fn not in sources}

    plan, used_targets, order_names = [], set(), {}
    for i, (t, _pos) in enumerate(ordered, start=1):
        base, tmp_path = have[t["id"]]
        cur = pl_manifest.get(str(t["id"]))
        if cur not in idx_by_file:
            cur = idx_by_name.get(nm.norm(base))
        if tmp_path is None and cur is None:
            continue
        if renumber_mode == "off" and cur and not tmp_path:
            target = cur
        else:
            idx = i if renumber_mode != "off" else next_idx
            target = nm.with_index(idx, base, width)
            n = 1
            while target in used_targets or target in occupied:
                target = nm.with_index(idx, f"{base} ({n})", width)
                n += 1
            if renumber_mode == "off":
                next_idx += 1
            occupied.add(target)
        used_targets.add(target)
        order_names[str(t["id"])] = target
        plan.append((target, cur, tmp_path))

    renamed = renumber(directory, plan, args.dry_run, report)
    if not args.dry_run:
        # 收尾只允许两种动作：删除本插件本次写进 .dsh_tmp 的文件、
        # 删掉已空的 .dsh_tmp 目录本身。别的任何文件都不碰。
        tmpbase = os.path.join(directory, ".dsh_tmp")
        if os.path.isdir(tmpbase):
            for fn in os.listdir(tmpbase):
                fp = os.path.join(tmpbase, fn)
                try:
                    os.unlink(fp)
                except OSError:
                    pass
            try:
                os.rmdir(tmpbase)
            except OSError:
                pass
        pl_manifest.update(order_names)   # dry-run 不写盘，免得记下没发生的改名

    status["renamed"] = renamed
    status["ordered"] = len(ordered)
    log(f"  重排：{len(ordered)} 首入编号，改名/落盘 {renamed} 个文件")
    return status


def run_loop(playlists, client, cfg, manifest, report, args):
    for pl in playlists:
        if args.playlist_id and pl["id"] != args.playlist_id:
            continue
        if args.dir and resolve_dir(pl.get("name") or "", cfg, existing_dirs(cfg["music_root"])) != args.dir:
            continue
        try:
            report["playlists"].append(sync_playlist(client, pl, cfg, manifest, report, args))
        except Exception as e:
            report["errors"].append(f"{pl.get('name')}: {e}")
            log(f"歌单 {pl.get('name')} 失败: {e}")
            traceback.print_exc()
        save_json(MANIFEST_PATH, manifest)


def finish(report, manifest):
    report["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save_json(os.path.join(STATE_DIR, "last-report.json"), report)
    save_json(MANIFEST_PATH, manifest)
    added = sum(len(p.get("new", [])) for p in report["playlists"])
    log(f"完成：新增 {added} 首，ncm 转换 {report['ncm_converted']} 个，"
        f"错误 {len(report['errors'])} 条")
    for e in report["errors"][:20]:
        log(f"  ! {e}")
    return added


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只报告不下载不改名")
    ap.add_argument("--dir", help="只处理目录名匹配的歌单")
    ap.add_argument("--playlist-id", type=int, help="只处理指定歌单 id")
    ap.add_argument("--max-new", type=int, default=0, help="每个歌单本次最多下载 N 首")
    ap.add_argument("--ncm-only", action="store_true", help="只做 ncm 转换")
    args = ap.parse_args()

    cfg = load_json(CONFIG_PATH, {})
    cfg.setdefault("music_root", "/vol1/1000/音乐")
    cfg.setdefault("level", "exhigh")
    cfg.setdefault("order", "auto")
    cfg.setdefault("concurrency", 4)
    cfg.setdefault("max_new_per_run", 0)

    os.makedirs(STATE_DIR, exist_ok=True)
    report = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "errors": [],
              "skipped_playlists": [], "playlists": [], "ncm_converted": 0}
    manifest = load_json(MANIFEST_PATH, {})
    unavailable.clear()
    unavailable.update(load_json(UNAVAILABLE_PATH, {}))

    if not os.path.isdir(cfg["music_root"]):
        log(f"音乐目录不存在: {cfg['music_root']}")
        return 2
    if not args.max_new and cfg.get("max_new_per_run"):
        args.max_new = int(cfg["max_new_per_run"])

    client = ne.Client()
    if not args.ncm_only:
        logged = client.logged_in()
        if logged:
            log(f"登录账号：{client.nickname} (uid={client.uid})")
            playlists = [pl for pl in client.user_playlists() if pl.get("id")]
            log(f"账号下歌单 {len(playlists)} 个")
        else:
            playlists = []
            if not (cfg.get("extra_playlist_ids") or []) and not args.playlist_id:
                log("未登录：先跑 node login.mjs 扫码，再执行同步")
                return 3
            log("未登录，本轮只同步 config.extra_playlist_ids 里的公开歌单")

        known = {pl["id"] for pl in playlists}
        wanted = [int(x) for x in (cfg.get("extra_playlist_ids") or [])]
        if args.playlist_id:
            wanted = [args.playlist_id]
        for pid in wanted:
            if pid in known:
                continue
            meta, _ = client.playlist_trackids(pid)
            playlists.append({"id": pid, "name": meta.get("name") or str(pid)})
            log(f"  追加跟踪歌单 {pid}《{meta.get('name')}》")

        only = set(cfg.get("only_dirs") or [])
        if only and logged:
            playlists = [pl for pl in playlists
                         if resolve_dir(pl.get("name") or "", cfg, existing_dirs(cfg["music_root"])) in only]
        run_loop(playlists, client, cfg, manifest, report, args)

    convert_ncm_files(cfg["music_root"], cfg, report)
    finish(report, manifest)
    return 0


if __name__ == "__main__":
    sys.exit(main())