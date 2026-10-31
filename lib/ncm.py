"""NCM 解密：还原原始音频 + 元数据 + 封面。

布局（顺序解析，不依赖固定 142 偏移）：
  magic(10) | key_len(4) | key_data(key_len) | meta_len(4) | meta_data(meta_len)
  | 填充(5) | img_len(4) | img_data(img_len) | 音频(CR4 加密)

meta 之后这段填充在不同客户端版本里有差异，所以解析器对 [5,9,0,4,13] 逐个尝试，
用解密后音频头的魔数(ID3 / fLaC / MPEG sync)自证，选第一个通过的。
"""
import base64, json, os, struct, subprocess, tempfile

from Cryptodome.Cipher import AES
from Cryptodome.Util import Padding

MAGIC = b"CTENFDAM"
KEY_BOX = bytes([0x68, 0x7A, 0x48, 0x52, 0x41, 0x6D, 0x73, 0x6F,
                 0x35, 0x6B, 0x49, 0x6E, 0x62, 0x61, 0x78, 0x57])   # hzHRAmso5kInbaxW
META_BOX = bytes([0x23, 0x31, 0x34, 0x6C, 0x6A, 0x6B, 0x5F, 0x21,
                  0x5C, 0x5D, 0x26, 0x30, 0x55, 0x3C, 0x27, 0x28])  # #14ljk_!\]&0U<'(
GAP_CANDIDATES = (5, 9, 0, 4, 13)


class NcmError(Exception):
    pass


def _aes_ecb_decrypt(blob, key):
    c = AES.new(key, AES.MODE_ECB)
    return c.decrypt(blob)


def _unpad(data):
    try:
        return Padding.unpad(data, 16)
    except ValueError:
        return data


def _rc4(data, key):
    box = list(range(256))
    j = 0
    klen = len(key)
    for i in range(256):
        j = (j + box[i] + key[i % klen]) & 0xFF
        box[i], box[j] = box[j], box[i]
    out = bytearray(data)
    for k in range(len(out)):
        i = (k + 1) & 0xFF
        j = (box[i] + i) & 0xFF
        out[k] ^= box[(box[i] + box[j]) & 0xFF]
    return bytes(out)


def _looks_like_audio(buf):
    if len(buf) < 4:
        return None
    if buf[:3] == b"ID3":
        return "mp3"
    if buf[:4] == b"fLaC":
        return "flac"
    if buf[0] == 0xFF and (buf[1] & 0xE0) == 0xE0:
        return "mp3"
    if buf[:4] == b"OggS":
        return "ogg"
    if buf[:4] in (b"RIFF",):
        return "wav"
    return None


def parse_header(path):
    """返回 (rc4_key, meta_dict, cover_bytes, audio_offset_candidates_checked)"""
    with open(path, "rb") as f:
        data = f.read()
    if not data.startswith(MAGIC):
        raise NcmError("不是 NCM 文件（magic 不符）")
    pos = 10
    key_len = struct.unpack_from("<I", data, pos)[0]
    pos += 4
    if not 0 < key_len < 4096:
        raise NcmError(f"key 长度异常 {key_len}")
    key_blob = bytes(b ^ 0x64 for b in data[pos:pos + key_len])
    pos += key_len
    rc4_key = _unpad(_aes_ecb_decrypt(key_blob, KEY_BOX))[17:]

    meta_len = struct.unpack_from("<I", data, pos)[0]
    pos += 4
    meta = {}
    if 0 < meta_len < 1 << 20:
        meta_blob = bytes(b ^ 0x63 for b in data[pos:pos + meta_len])
        pos += meta_len
        try:
            inner = base64.b64decode(meta_blob[22:])
            plain = _unpad(_aes_ecb_decrypt(_align(inner), META_BOX))
            if plain[:6] == b"music:":
                plain = plain[6:]
            meta = json.loads(plain.decode("utf-8", "replace").strip() or "{}")
        except Exception:
            meta = {}
    return rc4_key, meta, data, pos


def _align(blob):
    """AES 分组补齐到 16 的整数倍，避免部分样本尾部被截断报错。"""
    rem = len(blob) % 16
    return blob + b"\x00" * (16 - rem) if rem else blob


def decrypt(path):
    """返回 dict: audio(bytes) / ext / meta / cover"""
    rc4_key, meta, data, pos = parse_header(path)
    last_err = None
    for gap in GAP_CANDIDATES:
        p = pos + gap
        if p + 4 > len(data):
            continue
        img_len = struct.unpack_from("<I", data, p)[0]
        p += 4
        if not 0 < img_len < 32 << 20 or p + img_len > len(data):
            continue
        cover = data[p:p + img_len]
        p += img_len
        if p >= len(data):
            continue
        audio = _rc4(data[p:], rc4_key)
        kind = _looks_like_audio(audio)
        if kind:
            return {"audio": audio, "ext": kind, "meta": meta,
                    "cover": cover if cover[:2] == b"\xff\xd8" or cover[:8] == b"\x89PNG\r\n\x1a\n" else b"",
                    "gap": gap}
        last_err = f"gap={gap} 解码后不是可识别音频（{audio[:4]!r}）"
    raise NcmError(last_err or "音频数据解析失败")


def meta_fields(meta):
    artists = meta.get("artist") or []
    names = []
    if isinstance(artists, list):
        for a in artists:
            if isinstance(a, list):
                # 真实布局是 [[艺人名, 艺人id], ...]，只取第一项
                if a and a[0]:
                    names.append(str(a[0]))
            elif isinstance(a, str):
                names.append(a)
    elif isinstance(artists, str):
        names = [artists]
    title = (meta.get("musicName") or "").strip()
    album = (meta.get("album") or "").strip()
    fmt = (meta.get("format") or "").lower()
    return title, names, album, fmt


def convert(ncm_path, out_mp3, target_bitrate="320k", ffmpeg="/usr/bin/ffmpeg"):
    """解密 + 转 mp3 + 打标签，返回 (out_mp3, meta_titles)。"""
    d = decrypt(ncm_path)
    title, artists, album, _fmt = meta_fields(d["meta"])
    tmpdir = tempfile.mkdtemp(prefix="ncm-")
    try:
        raw = os.path.join(tmpdir, "raw." + d["ext"])
        with open(raw, "wb") as f:
            f.write(d["audio"])
        cover_path = None
        if d["cover"]:
            cover_path = os.path.join(tmpdir, "cover.jpg")
            with open(cover_path, "wb") as f:
                f.write(d["cover"])
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", raw]
        if cover_path:
            cmd += ["-i", cover_path, "-map", "0:a", "-map", "1:v",
                    "-c:v", "copy", "-disposition:v", "attached_pic"]
        cmd += ["-c:a", "libmp3lame", "-b:a", target_bitrate, "-id3v2_version", "3"]
        if title:
            cmd += ["-metadata", f"title={title}"]
        if artists:
            cmd += ["-metadata", f"artist={'/'.join(artists)}"]
        if album:
            cmd += ["-metadata", f"album={album}"]
        cmd += [out_mp3]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0 or not os.path.exists(out_mp3):
            raise NcmError(f"ffmpeg 失败: {proc.stderr.strip()[:200]}")
        return out_mp3, title, artists, album
    finally:
        for name in os.listdir(tmpdir):
            try:
                os.unlink(os.path.join(tmpdir, name))
            except OSError:
                pass
        os.rmdir(tmpdir)
