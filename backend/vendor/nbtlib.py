# -*- coding: utf-8 -*-
"""
nbtlib.py - 零依赖 NBT 读写器
支持:
  - Java 版 NBT: 大端序, gzip 压缩 (.litematic / .nbt / .schematic)
  - 基岩版 NBT: 小端序, 无压缩 (.mcstructure / level.dat 等)
"""
import gzip
import struct
from io import BytesIO

TAG_END = 0
TAG_BYTE = 1
TAG_SHORT = 2
TAG_INT = 3
TAG_LONG = 4
TAG_FLOAT = 5
TAG_DOUBLE = 6
TAG_BYTE_ARRAY = 7
TAG_STRING = 8
TAG_LIST = 9
TAG_COMPOUND = 10
TAG_INT_ARRAY = 11
TAG_LONG_ARRAY = 12


class NBTError(Exception):
    """NBT 解析/写入错误"""


# ---------------- Tag 基类 ----------------

class Tag:
    tag_id = -1

    def write_payload(self, buf, endian):
        raise NotImplementedError

    def write_named(self, buf, name, endian):
        buf.write(bytes([self.tag_id]))
        _write_string(buf, name, endian)
        self.write_payload(buf, endian)


class ByteTag(Tag):
    tag_id = TAG_BYTE

    def __init__(self, value=0):
        self.value = int(value) & 0xFF
        if self.value >= 128:
            self.value -= 256

    def write_payload(self, buf, endian):
        buf.write(struct.pack("b", self.value))

    def __repr__(self):
        return f"ByteTag({self.value})"


class ShortTag(Tag):
    tag_id = TAG_SHORT

    def __init__(self, value=0):
        self.value = int(value)

    def write_payload(self, buf, endian):
        buf.write(struct.pack(endian + "h", self.value))

    def __repr__(self):
        return f"ShortTag({self.value})"


class IntTag(Tag):
    tag_id = TAG_INT

    def __init__(self, value=0):
        self.value = int(value)

    def write_payload(self, buf, endian):
        buf.write(struct.pack(endian + "i", self.value))

    def __repr__(self):
        return f"IntTag({self.value})"


class LongTag(Tag):
    tag_id = TAG_LONG

    def __init__(self, value=0):
        self.value = int(value)

    def write_payload(self, buf, endian):
        buf.write(struct.pack(endian + "q", self.value))

    def __repr__(self):
        return f"LongTag({self.value})"


class FloatTag(Tag):
    tag_id = TAG_FLOAT

    def __init__(self, value=0.0):
        self.value = float(value)

    def write_payload(self, buf, endian):
        buf.write(struct.pack(endian + "f", self.value))


class DoubleTag(Tag):
    tag_id = TAG_DOUBLE

    def __init__(self, value=0.0):
        self.value = float(value)

    def write_payload(self, buf, endian):
        buf.write(struct.pack(endian + "d", self.value))


class ByteArrayTag(Tag):
    tag_id = TAG_BYTE_ARRAY

    def __init__(self, value=b""):
        self.value = bytes(value)

    def write_payload(self, buf, endian):
        buf.write(struct.pack(endian + "i", len(self.value)))
        buf.write(self.value)


class StringTag(Tag):
    tag_id = TAG_STRING

    def __init__(self, value=""):
        self.value = str(value)

    def write_payload(self, buf, endian):
        _write_string(buf, self.value, endian)

    def __repr__(self):
        return f"StringTag({self.value!r})"


class ListTag(Tag):
    tag_id = TAG_LIST

    def __init__(self, item_type=TAG_END, items=None):
        self.item_type = item_type
        self.items = list(items) if items else []
        if self.items and item_type == TAG_END:
            self.item_type = self.items[0].tag_id

    def write_payload(self, buf, endian):
        buf.write(bytes([self.item_type]))
        buf.write(struct.pack(endian + "i", len(self.items)))
        for item in self.items:
            item.write_payload(buf, endian)

    def __repr__(self):
        return f"ListTag(type={self.item_type}, len={len(self.items)})"


class CompoundTag(Tag):
    tag_id = TAG_COMPOUND

    def __init__(self, value=None):
        self.value = dict(value) if value else {}

    def __setitem__(self, k, v):
        self.value[k] = v

    def __getitem__(self, k):
        return self.value[k]

    def __contains__(self, k):
        return k in self.value

    def get(self, k, default=None):
        return self.value.get(k, default)

    def write_payload(self, buf, endian):
        for name, tag in self.value.items():
            tag.write_named(buf, name, endian)
        buf.write(b"\x00")

    def __repr__(self):
        return f"CompoundTag({list(self.value.keys())})"


class IntArrayTag(Tag):
    tag_id = TAG_INT_ARRAY

    def __init__(self, value=None):
        self.value = list(value) if value else []

    def write_payload(self, buf, endian):
        buf.write(struct.pack(endian + "i", len(self.value)))
        buf.write(struct.pack(endian + f"{len(self.value)}i", *self.value))


class LongArrayTag(Tag):
    tag_id = TAG_LONG_ARRAY

    def __init__(self, value=None):
        self.value = list(value) if value else []

    def write_payload(self, buf, endian):
        buf.write(struct.pack(endian + "i", len(self.value)))
        buf.write(struct.pack(endian + f"{len(self.value)}q", *self.value))


_TAG_CLASSES = {
    TAG_BYTE: ByteTag, TAG_SHORT: ShortTag, TAG_INT: IntTag, TAG_LONG: LongTag,
    TAG_FLOAT: FloatTag, TAG_DOUBLE: DoubleTag, TAG_BYTE_ARRAY: ByteArrayTag,
    TAG_STRING: StringTag, TAG_LIST: ListTag, TAG_COMPOUND: CompoundTag,
    TAG_INT_ARRAY: IntArrayTag, TAG_LONG_ARRAY: LongArrayTag,
}


# ---------------- 读取 ----------------

class _Reader:
    def __init__(self, data, endian):
        self.buf = BytesIO(data)
        self.endian = endian

    def read(self, fmt):
        size = struct.calcsize(fmt)
        b = self.buf.read(size)
        if len(b) < size:
            raise NBTError("NBT 数据被截断（文件可能已损坏）")
        return struct.unpack(fmt, b)[0]

    def read_payload(self, tag_id):
        e = self.endian
        if tag_id == TAG_BYTE:
            return ByteTag(self.read("b"))
        if tag_id == TAG_SHORT:
            return ShortTag(self.read(e + "h"))
        if tag_id == TAG_INT:
            return IntTag(self.read(e + "i"))
        if tag_id == TAG_LONG:
            return LongTag(self.read(e + "q"))
        if tag_id == TAG_FLOAT:
            return FloatTag(self.read(e + "f"))
        if tag_id == TAG_DOUBLE:
            return DoubleTag(self.read(e + "d"))
        if tag_id == TAG_BYTE_ARRAY:
            n = self.read(e + "i")
            b = self.buf.read(n)
            if len(b) < n:
                raise NBTError("NBT 字节数组被截断")
            return ByteArrayTag(b)
        if tag_id == TAG_STRING:
            return StringTag(self._read_string())
        if tag_id == TAG_LIST:
            item_type = self.read("b")
            n = self.read(e + "i")
            if n < 0:
                raise NBTError("NBT 列表长度为负，文件损坏")
            return ListTag(item_type, [self.read_payload(item_type) for _ in range(n)])
        if tag_id == TAG_COMPOUND:
            comp = CompoundTag()
            while True:
                t = self.read("b")
                if t == TAG_END:
                    break
                name = self._read_string()
                comp[name] = self.read_payload(t)
            return comp
        if tag_id == TAG_INT_ARRAY:
            n = self.read(e + "i")
            return IntArrayTag([self.read(e + "i") for _ in range(n)])
        if tag_id == TAG_LONG_ARRAY:
            n = self.read(e + "i")
            return LongArrayTag([self.read(e + "q") for _ in range(n)])
        raise NBTError(f"未知的 NBT 标签类型: {tag_id}")

    def _read_string(self):
        n = self.read(self.endian + "H")
        b = self.buf.read(n)
        if len(b) < n:
            raise NBTError("NBT 字符串被截断")
        return b.decode("utf-8", errors="replace")


def read_nbt(data, endian=">", gzipped="auto"):
    """读取 NBT 数据，返回 (root_name, CompoundTag)。
    endian: '>' Java 大端, '<' 基岩小端
    gzipped: True/False/'auto' 自动检测 gzip 魔数
    """
    if gzipped == "auto":
        gzipped = data[:2] == b"\x1f\x8b"
    if gzipped:
        try:
            data = gzip.decompress(data)
        except OSError as e:
            raise NBTError(f"gzip 解压失败（文件可能损坏或不是有效的 NBT 文件）: {e}")
    r = _Reader(data, endian)
    tag_id = r.read("b")
    if tag_id != TAG_COMPOUND:
        raise NBTError(f"NBT 根标签应为 Compound(10)，实际为 {tag_id}（可能不是 NBT 文件）")
    name = r._read_string()
    return name, r.read_payload(tag_id)


def load_java_nbt(path):
    """读取 Java 版 NBT 文件（大端 + 自动检测 gzip）"""
    with open(path, "rb") as f:
        data = f.read()
    if len(data) == 0:
        raise NBTError("文件为空")
    return read_nbt(data, endian=">", gzipped="auto")


def load_bedrock_nbt(path):
    """读取基岩版 NBT 文件（小端，无压缩）"""
    with open(path, "rb") as f:
        data = f.read()
    if len(data) == 0:
        raise NBTError("文件为空")
    return read_nbt(data, endian="<", gzipped=False)


# ---------------- 写入 ----------------

def _write_string(buf, s, endian):
    b = s.encode("utf-8")
    buf.write(struct.pack(endian + "H", len(b)))
    buf.write(b)


def write_nbt(root_name, root_compound, endian="<"):
    """序列化 NBT。基岩版用 '<'，Java 用 '>'。"""
    buf = BytesIO()
    buf.write(bytes([TAG_COMPOUND]))
    _write_string(buf, root_name, endian)
    root_compound.write_payload(buf, endian)
    return buf.getvalue()


def save_bedrock_nbt(path, root_compound, root_name=""):
    """保存为基岩版 NBT（小端、无压缩）—— .mcstructure 使用此格式"""
    with open(path, "wb") as f:
        f.write(write_nbt(root_name, root_compound, endian="<"))


def save_java_nbt(path, root_compound, root_name="", gzipped=True):
    """保存为 Java 版 NBT（大端），测试样本生成用"""
    data = write_nbt(root_name, root_compound, endian=">")
    with open(path, "wb") as f:
        f.write(gzip.compress(data) if gzipped else data)
