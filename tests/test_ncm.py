"""NCM 往返测试：合成一个 .ncm，再用 lib/ncm.py 解开，比对字节。"""
import os, struct, sys, json, base64, subprocess, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "lib"))

from Cryptodome.Cipher import AES
from Cryptodome.Util import Padding
import ncm as ncm_mod


def aes_enc(data, key):
    c = AES.new(key, AES.MODE_ECB)
    return c.encrypt(Padding.pad(data, 16))


def rc4(data, key):
    return ncm_mod._rc4(data, key)      # 加密解密同构


def build_ncm(audio, meta, cover, rc4_key):
    out = bytearray(b"CTENFDAM")
    out += b"\x01\x70"
    key_plain = b"neteasecloudmusic" + rc4_key
    key_blob = bytes(b ^ 0x64 for b in aes_enc(key_plain, ncm_mod.KEY_BOX))
    out += struct.pack("<I", len(key_blob)) + key_blob

    meta_plain = b"music:" + json.dumps(meta, ensure_ascii=False).encode()
    meta_inner = aes_enc(meta_plain, ncm_mod.META_BOX)
    meta_b64 = base64.b64encode(meta_inner)
    meta_blob = bytes(b ^ 0x63 for b in (b"163 key(Don't modify):" + meta_b64))
    out += struct.pack("<I", len(meta_blob)) + meta_blob

    out += b"\x00" * 5                                            # 标准 5 字节间隔
    out += struct.pack("<I", len(cover)) + cover
    out += rc4(audio, rc4_key)
    return bytes(out)


def main():
    tmp = tempfile.mkdtemp(prefix="ncmtest-")
    src_mp3 = os.path.join(tmp, "src.mp3")
    # 用 ffmpeg 生成一段测试音频，不依赖外部素材
    subprocess.run(["/usr/bin/ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=25",
                    "-c:a", "libmp3lame", "-b:a", "192k", src_mp3], check=True)
    audio = open(src_mp3, "rb").read()
    cover_path = os.path.join(tmp, "cover.jpg")
    subprocess.run(["/usr/bin/ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=red:s=300x300:d=1",
                    "-frames:v", "1", cover_path], check=True)
    cover = open(cover_path, "rb").read()
    meta = {"musicName": "测试曲目", "artist": [["测试歌手", 1]], "album": "测试专辑",
            "format": "mp3", "bitrate": 192000}
    rc4_key = bytes(range(1, 17))

    ncm_path = os.path.join(tmp, "sample.ncm")
    open(ncm_path, "wb").write(build_ncm(audio, meta, cover, rc4_key))

    d = ncm_mod.decrypt(ncm_path)
    assert d["ext"] == "mp3", d["ext"]
    assert d["audio"] == audio, "音频字节不一致"
    assert d["cover"] == cover, "封面不一致"
    title, artists, album, fmt = ncm_mod.meta_fields(d["meta"])
    assert title == "测试曲目" and artists == ["测试歌手"] and album == "测试专辑", (title, artists, album)
    print(f"  decrypt OK (gap={d['gap']}, {len(audio)} 字节音频, 封面 {len(cover)} 字节)")

    out_mp3 = os.path.join(tmp, "out.mp3")
    ncm_mod.convert(ncm_path, out_mp3, "320k", "/usr/bin/ffmpeg")
    probe = subprocess.run(["/usr/bin/ffprobe", "-v", "quiet", "-print_format", "json",
                            "-show_format", "-show_streams", out_mp3],
                           capture_output=True, text=True)
    info = json.loads(probe.stdout)
    tags = info["format"].get("tags", {})
    kinds = [(s["codec_type"], s.get("codec_name")) for s in info["streams"]]
    assert any(k[0] == "audio" and k[1] == "mp3" for k in kinds), kinds
    assert any(k[0] == "video" for k in kinds), f"封面未写入 {kinds}"
    assert tags.get("title") == "测试曲目", tags
    print(f"  convert OK: {kinds} tags={ {k: tags[k] for k in ('title','artist','album') if k in tags} }")

    # 变体：9 字节间隔也要能解
    for gap in (9, 0):
        p2 = os.path.join(tmp, f"v{gap}.ncm")
        blob = bytearray(build_ncm(audio, meta, cover, rc4_key))
        # 重建：5 → gap
        body = build_ncm(audio, {}, b"", rc4_key)[:0]  # noop
        raw = build_ncm(audio, meta, cover, rc4_key)
        # 找到 meta 结束位置，改写间隔长度
        import re
        pos = 10
        kl = struct.unpack_from("<I", raw, pos)[0]; pos += 4 + kl
        ml = struct.unpack_from("<I", raw, pos)[0]; pos += 4 + ml
        new = raw[:pos] + b"\x00" * gap + raw[pos + 5:]
        open(p2, "wb").write(new)
        dd = ncm_mod.decrypt(p2)
        assert dd["audio"] == audio, f"gap={gap} 解不出来"
        print(f"  gap={gap} 变体 OK")
    print("NCM: 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
