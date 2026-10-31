"""离线回归：用假 Client 跑一遍 sync_playlist，覆盖三个曾经踩过的坑。

  1) 命名丢了 .mp3 扩展名（with_index 不补后缀）
  2) 下载好的 tmp 文件在重排前被 tmpdir 一起删掉
  3) 已有文件被算成新歌重复下载

不碰网络、不碰真实音乐库，全部在临时目录里跑。
"""
import os, shutil, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "lib"))

import sync
import names as nm


class FakeClient:
    """最小可用的假接口：两首已在库、一首新歌。"""

    def __init__(self):
        self.songs_db = {
            1: {"id": 1, "name": "老歌一号", "ar": [{"name": "A"}], "al": {"name": "专辑1", "picUrl": ""}},
            2: {"id": 2, "name": "老歌二号", "ar": [{"name": "B"}], "al": {"name": "专辑1", "picUrl": ""}},
            3: {"id": 3, "name": "新歌三号", "ar": [{"name": "C"}], "al": {"name": "专辑2", "picUrl": ""}},
        }
        self.downloaded = []

    def playlist_trackids(self, pid):
        return ({"id": pid, "name": "测试歌单"}, [{"id": 3, "at": 300}, {"id": 2, "at": 200}, {"id": 1, "at": 100}])

    def songs(self, ids, chunk=100):
        return {i: self.songs_db[i] for i in ids if i in self.songs_db}

    def song_url(self, sid, br=320000):
        return ("http://fake/audio.mp3", 200, 0, br)

    def stream(self, url, dest, timeout=120):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(b"\xff\xfb" + b"\x00" * (300 * 1024))     # 过 200KB 门槛
        self.downloaded.append(dest)
        return 300 * 1024


def main():
    tmp = tempfile.mkdtemp(prefix="synctest-")
    root = os.path.join(tmp, "音乐")
    pl_dir = os.path.join(root, "测试歌单")
    os.makedirs(pl_dir)
    for name in ("001_A - 老歌一号.mp3", "002_B - 老歌二号.mp3"):
        with open(os.path.join(pl_dir, name), "wb") as f:
            f.write(b"ID3" + b"\x00" * 100)

    cfg = {"music_root": root, "level": "exhigh", "renumber": "always",
           "order": "auto", "concurrency": 1, "create_missing_dirs": False,
           "dir_overrides": {}, "only_dirs": [], "ncm_scan": False}

    sync.STATE_DIR = os.path.join(tmp, "state")
    sync.MANIFEST_PATH = os.path.join(sync.STATE_DIR, "manifest.json")
    sync.UNAVAILABLE_PATH = os.path.join(sync.STATE_DIR, "unavailable.json")
    os.makedirs(sync.STATE_DIR, exist_ok=True)
    sync.unavailable.clear()

    # 打标签换成直接搬文件，测试不依赖 ffmpeg
    sync.tag_mp3 = lambda src, dest, title, artists, album, cover, tmpdir: shutil.move(src, dest)

    client = FakeClient()
    manifest = {}
    report = {"errors": [], "skipped_playlists": [], "playlists": [], "ncm_converted": 0}
    args = type("A", (), {"dry_run": False, "max_new": 0})()

    sync.sync_playlist(client, {"id": 7, "name": "测试歌单"}, cfg, manifest, report, args)

    files = sorted(f for f in os.listdir(pl_dir) if f.endswith(".mp3"))
    assert len(files) == 3, f"应有 3 首，实际 {files}"
    assert all(f.endswith(".mp3") for f in files), f"扩展名丢失: {files}"
    # 新歌 3 的添加时间最新 → 001
    assert files[0].startswith("001_C - 新歌三号"), f"排序不对: {files}"
    assert len(client.downloaded) == 1, f"重复下载了: {client.downloaded}"
    assert not os.path.exists(os.path.join(sync.STATE_DIR, "tmp", "7")), "临时目录没清掉"
    print("  3 首齐全、扩展名正确、新歌排 001、只下载 1 次、临时目录已清")
    print("sync: 全部通过")
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
