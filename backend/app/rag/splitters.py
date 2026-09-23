# 文件作用：按劳动法律文本结构将文档切分为可检索的文本块。
"""Domain-aware LangChain text splitters for labor-law documents."""

from __future__ import annotations

import re
from bisect import bisect_right
from collections.abc import Iterable

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.rag.constants import CURRENT_SPLITTER_VERSION
from app.services.text import TextCleaner

# 匹配中文法律条款、编号项和列表标记的正则表达式。
_CLAUSE_MARKER = re.compile(
    r"第[〇零一二三四五六七八九十百千万两0-9]+条"
    r"(?:之[〇零一二三四五六七八九十百千万两0-9]+)?"
    r"|[（(][一二三四五六七八九十百千万]+[）)]"
    r"|[一二三四五六七八九十百千万]+、"
    r"|[0-9]+[、.](?![0-9])"
)
# 识别中文及英文句子结束标点的正则表达式。
_SENTENCE_END = re.compile(r"[。；;！？!?]")
# 劳动法律文本切分器使用的优先分隔符列表。
_LEGAL_SEPARATORS = [
    r"(?=第[〇零一二三四五六七八九十百千万两0-9]+条(?:之[〇零一二三四五六七八九十百千万两0-9]+)?)",
    r"(?=[（(][一二三四五六七八九十百千万]+[）)])",
    r"(?=[一二三四五六七八九十百千万]+、)",
    r"(?=[0-9]+[、.](?![0-9]))",
    r"\n\n",
    r"\n",
    "。",
    "；",
    "，",
    "",
]


class LegalTextSplitter(RecursiveCharacterTextSplitter):
    """Recursive splitter that preserves legal clause boundaries when possible."""

    def __init__(self, chunk_size: int = 600, chunk_overlap: int = 100) -> None:
        """初始化文本切分器的块长度和重叠长度参数。

        参数：
        chunk_size：文本切分时单个分块的目标长度。
        chunk_overlap：相邻文本分块之间保留的重叠长度。
        """
        if chunk_size <= 0:
            raise ValueError("chunk_size must be greater than zero")
        if chunk_overlap < 0 or chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must be between zero and chunk_size - 1")

        super().__init__(
            separators=_LEGAL_SEPARATORS,
            keep_separator="end",
            is_separator_regex=True,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            length_function=len,
        )
        # 成员变量 chunk_size：文本切分时单个分块的目标长度。
        self.chunk_size = chunk_size
        # 成员变量 chunk_overlap：相邻文本分块之间保留的重叠长度。
        self.chunk_overlap = chunk_overlap
        # 成员变量 version：当前配置或记录的版本号。
        self.version = CURRENT_SPLITTER_VERSION

    def split_text(self, text: str) -> list[str]:
        """按段落与边界规则将长文本切分为多个文本块。

        参数：
        text：待处理的文本内容。
        """
        # 局部变量 cleaned_text：经过清理并规范化后的文档文本。
        cleaned_text = TextCleaner.clean(text)
        if not cleaned_text:
            return []

        # 局部变量 clause_boundaries：法律条款分界位置集合。
        # 局部变量 paragraph_boundaries：段落分界位置集合。
        # 局部变量 sentence_boundaries：句子分界位置集合。
        clause_boundaries, paragraph_boundaries, sentence_boundaries = (
            self._find_boundaries(cleaned_text)
        )
        # 局部变量 chunks：当前文档或任务涉及的文本分块集合。
        chunks: list[str] = []
        # 局部变量 content_start：正文在原始文本中的起始字符偏移。
        content_start = 0
        while content_start < len(cleaned_text):
            # 局部变量 chunk_start：当前文本块的起始字符偏移。
            chunk_start = max(0, content_start - self.chunk_overlap)
            # 局部变量 max_end：当前文本块允许的最大结束偏移。
            max_end = min(len(cleaned_text), chunk_start + self.chunk_size)
            # 局部变量 end：当前文本块的结束字符偏移。
            end = self._choose_end(
                content_start,
                max_end,
                clause_boundaries,
                paragraph_boundaries,
                sentence_boundaries,
            )
            # 局部变量 content：当前文档、消息或文件的主体内容。
            content = cleaned_text[chunk_start:end].strip()
            if content:
                chunks.append(content)
            content_start = end
        return chunks

    def split_documents(self, documents: Iterable[Document]) -> list[Document]:
        """切分文档集合并保留原有元数据。

        参数：
        documents：需要处理的文档列表。
        """
        # 局部变量 chunks：当前文档或任务涉及的文本分块集合。
        chunks = super().split_documents(documents)
        # 局部变量 chunk_no：文档内部文本分块序号。
        # 局部变量 chunk：当前文本分块记录。
        for chunk_no, chunk in enumerate(chunks, start=1):
            chunk.metadata.setdefault("page", None)
            chunk.metadata["chunk_no"] = chunk_no
        return chunks

    @staticmethod
    def _find_boundaries(text: str) -> tuple[list[int], list[int], list[int]]:
        """查找文本中适合切分的段落和句子边界。

        参数：
        text：待处理的文本内容。
        """
        # 局部变量 clause_boundaries：法律条款分界位置集合。
        clause_boundaries: list[int] = []
        # 局部变量 match：当前正则匹配对象及其捕获内容。
        for match in _CLAUSE_MARKER.finditer(text):
            # 局部变量 start：当前文本块或子串的起始字符位置。
            start = match.start()
            if start == 0:
                continue

            # 局部变量 line_start：当前行在文本中的起始偏移。
            line_start = text.rfind("\n", 0, start) + 1
            # 局部变量 at_line_start：当前位置是否处于行首。
            at_line_start = not text[line_start:start].strip()
            # 局部变量 follows_clause_punctuation：切分点前是否为条款标点。
            follows_clause_punctuation = text[start - 1] in "。；;！？!?"
            if at_line_start or follows_clause_punctuation:
                clause_boundaries.append(start)

        # 局部变量 paragraph_boundaries：段落分界位置集合。
        paragraph_boundaries = [match.end() for match in re.finditer(r"\n+", text)]
        # 局部变量 sentence_boundaries：句子分界位置集合。
        sentence_boundaries = [match.end() for match in _SENTENCE_END.finditer(text)]
        return clause_boundaries, paragraph_boundaries, sentence_boundaries

    @staticmethod
    def _choose_end(
        content_start: int,
        max_end: int,
        clause_boundaries: list[int],
        paragraph_boundaries: list[int],
        sentence_boundaries: list[int],
    ) -> int:
        """在目标长度附近选择当前文本块的结束位置。

        参数：
        content_start：正文内容在输入文本中的起始字符偏移。
        max_end：当前文本块允许的最大结束偏移。
        clause_boundaries：法律条款分界位置集合。
        paragraph_boundaries：段落分界位置集合。
        sentence_boundaries：句子分界位置集合。
        """
        # 局部变量 boundaries：当前文本中所有候选分界位置。
        for boundaries in (
            clause_boundaries,
            paragraph_boundaries,
            sentence_boundaries,
        ):
            # 局部变量 boundary_index：当前文本切分边界在边界列表中的位置。
            boundary_index = bisect_right(boundaries, max_end) - 1
            if boundary_index >= 0 and boundaries[boundary_index] > content_start:
                return boundaries[boundary_index]
        return max_end
