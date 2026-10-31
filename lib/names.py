"""文件名规整：与现有音乐库保持完全一致的字符替换。"""
import re

# 现有库的既有约定（从已有 1384 个文件反推）：
#   ' - ' 连接 艺人 - 歌曲；多艺人间用全角 ／；. : ? " 等替换为对应全角字符
FULLWIDTH = {
    "/": "／", ":": "：", "?": "？", "\"": "＂", "*": "＊",
    "<": "＜", ">": "＞", "|": "｜", "\\": "＼", ".": "．",
    "\n": " ", "\r": " ", "\t": " ", "\x00": "",
}


def sanitize(text):
    out = []
    for ch in str(text):
        out.append(FULLWIDTH.get(ch, ch))
    s = "".join(out)
    s = re.sub(r"\s+", " ", s).strip()
    s = s.strip(" ．")
    return s or "未命名"


def track_name(artists, title):
    """艺人们 + ' - ' + 歌名（已规整，不含序号与扩展名）。"""
    names = [sanitize(a) for a in (artists or []) if a]
    t = sanitize(title)
    if not names:
        return t
    return "／".join(names) + " - " + t


# ext4/etx 单个文件名上限 255 字节；艺人多的时候（一首歌 20+ 位艺人）很容易超。
# 截断时保住"歌名"尾部，艺人串过长就先砍艺人。
NAME_BYTES = 240


def _fit(prefix, stem, ext):
    """把 prefix+stem+ext 压到 NAME_BYTES 字节内，优先保留 stem 的结尾。"""
    raw = (prefix + stem + ext).encode("utf-8")
    if len(raw) <= NAME_BYTES:
        return prefix + stem + ext
    budget = NAME_BYTES - len(prefix.encode()) - len(ext.encode())
    # 从尾部保留 stem，按字节裁掉超出的部分（不切坏多字节字符）
    tail = stem.encode("utf-8")[-budget:]
    while tail:
        try:
            text = tail.decode("utf-8")
            break
        except UnicodeDecodeError:
            tail = tail[1:]
    else:
        text = ""
    return f"{prefix}{text}{ext}"


def with_index(idx, name, width, ext=".mp3"):
    stem = name[:-len(ext)] if name.lower().endswith(ext) else name
    return _fit(f"{idx:0{width}d}_", stem, ext)


def strip_index(filename):
    """去掉 NNN_ 前缀与扩展名，返回 (maybe_index, rest)。"""
    base = filename.rsplit(".", 1)[0] if "." in filename else filename
    m = re.match(r"^(\d+)_(.*)$", base)
    if m:
        return int(m.group(1)), m.group(2)
    return None, base


def title_of(name):
    """从 '艺人 - 歌名' 里切出歌名；切不到就整体返回。"""
    if " - " in name:
        return name.rsplit(" - ", 1)[1]
    return name


def norm(text):
    """比较用的宽松形式：全角半角、空格、大小写不敏感。"""
    s = str(text)
    table = str.maketrans({
        "．": ".", "：" : ":", "？": "?", "＂": "\"", "＊": "*",
        "＜": "<", "＞": ">", "｜": "|", "＼": "\\", "／" : "/",
        "（": "(", "）": ")", ",": ",", "·": ".",
    })
    s = s.translate(table).lower()
    s = re.sub(r"[\s\u3000]+", "", s)
    s = re.sub(r"[\.…・,，。]+$", "", s)
    return s
