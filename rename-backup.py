#!/usr/bin/env python3
"""记录/恢复音乐目录的文件名快照。

  python3 rename-backup.py                # 快照到 state/renames/<时间>.json
  python3 rename-backup.py --list         # 列出已有快照
  python3 rename-backup.py --restore FILE # 按快照把名字改回去

快照只记文件名（不拷音频），按目录分组，恢复时先校验文件还在。
"""
import argparse, json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from sync import load_json, save_json, CONFIG_PATH, STATE_DIR

SNAP_DIR = os.path.join(STATE_DIR, "renames")


def snapshot():
    cfg = load_json(CONFIG_PATH, {})
    root = cfg.get("music_root", "/vol1/1000/音乐")
    data = {"root": root, "time": time.strftime("%Y-%m-%d %H:%M:%S"), "dirs": {}}
    for d in sorted(os.listdir(root)):
        path = os.path.join(root, d)
        if not os.path.isdir(path) or d.startswith("."):
            continue
        data["dirs"][d] = sorted(f for f in os.listdir(path) if f.lower().endswith(".mp3"))
    os.makedirs(SNAP_DIR, exist_ok=True)
    out = os.path.join(SNAP_DIR, time.strftime("%Y%m%d-%H%M%S") + ".json")
    save_json(out, data)
    total = sum(len(v) for v in data["dirs"].values())
    print(f"快照已写入 {out}（{len(data['dirs'])} 个目录，{total} 个文件）")
    return 0


def restore(path):
    data = load_json(path, None)
    if not data:
        print(f"读不到快照 {path}")
        return 2
    root = data["root"]
    fixed = missing = 0
    for d, names in data["dirs"].items():
        cur = os.path.join(root, d)
        if not os.path.isdir(cur):
            continue
        present = {f for f in os.listdir(cur) if f.lower().endswith(".mp3")}
        # 快照里每个名字，找同目录下"去掉编号后同名"的文件改回去
        for want in names:
            if want in present:
                continue
            stem = want.rsplit(".", 1)[0]
            tail = stem.split("_", 1)[1] if "_" in stem else stem
            src = next((f for f in present if f.rsplit(".", 1)[0].endswith(tail)), None)
            if src is None:
                missing += 1
                continue
            os.rename(os.path.join(cur, src), os.path.join(cur, want))
            present.discard(src)
            present.add(want)
            fixed += 1
    print(f"恢复完成：改名 {fixed}，找不到 {missing}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--restore", metavar="FILE")
    a = ap.parse_args()
    if a.list:
        if os.path.isdir(SNAP_DIR):
            for f in sorted(os.listdir(SNAP_DIR)):
                print(os.path.join(SNAP_DIR, f))
        return 0
    if a.restore:
        return restore(a.restore)
    return snapshot()


if __name__ == "__main__":
    sys.exit(main())
