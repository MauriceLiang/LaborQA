"""清理检索与生成流程中的文本空白，统一换行和水平空格格式。"""

import re

# 匹配一行内连续的水平空白字符，不跨越换行。
_HORIZONTAL_WHITESPACE = re.compile(r"[^\S\n]+")
# 匹配三个及以上连续换行，用于压缩过多的空白段落。
_EXCESSIVE_NEWLINES = re.compile(r"\n{3,}")


class TextCleaner:
    """提供不改变正文字符、仅规范空白的文本清理方法。"""

    @staticmethod
    def clean(text: str) -> str:
        """规范文本空白，同时保留所有非空白字符。

        参数：text 为需要清理的原始文本。
        返回：去除首尾空白、统一换行并压缩多余空行后的文本。
        """
        # 将不同操作系统的换行统一，便于后续逐行处理。
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        # 对拆分出的每一行清理水平空白及行首、行尾空格。
        lines = [
            _HORIZONTAL_WHITESPACE.sub(" ", line).strip()
            for line in normalized.split("\n")
        ]
        # 重新拼接清理后的各行，并保留必要的段落间隔。
        normalized = "\n".join(lines)
        normalized = _EXCESSIVE_NEWLINES.sub("\n\n", normalized)
        return normalized.strip()
