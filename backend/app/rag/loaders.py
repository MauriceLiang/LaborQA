# 文件作用：加载项目内置的劳动法律资料并转换为 LangChain 文档对象。
"""把知识库源文件解析为 LangChain 文档对象，供后续切分和索引。"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from pathlib import Path

from langchain_core.document_loaders import BaseLoader
from langchain_core.documents import Document

from app.core.config import Settings, settings
from app.services.document_parser import ParserFactory


class LaborQADocumentLoader(BaseLoader):
    """将受支持的源文件解析为单个 LangChain Document。

    解析交由 ParserFactory 执行，以复用 DOC 转换、表格提取和项目错误处理。
    """

    def __init__(
        self,
        path: str | Path,
        file_type: str | None = None,
        *,
        config: Settings = settings,
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        """保存文件路径、解析配置和可附加的文档元数据。

        参数：path 为待解析文件；file_type 为可选显式格式；config 为应用设置；
        metadata 为需要附加到 LangChain 文档上的来源信息。
        """
        # 成员变量 path：当前文件或索引所在的路径。
        self.path = Path(path)  # 规范化后的待解析文件路径。
        # 成员变量 file_type：根据文件扩展名识别出的文档类型。
        self.file_type = file_type or self.path.suffix.lstrip(".")  # 使用传入格式或从扩展名推断格式。
        # 成员变量 config：文档解析 Settings，决定旧版 DOC 使用的转换器及相关路径；加载器据此选择解析或转换流程。
        self.config = config  # 文档解析所使用的应用配置。
        # 成员变量 metadata：调用方传入的文档来源信息副本，随解析生成的 LangChain Document 一并返回，供切块后写入数据库和索引。
        self.metadata = dict(metadata or {})  # 复制调用方元数据，避免共享可变映射。

    def lazy_load(self) -> Iterator[Document]:
        """延迟解析文件并产出包含正文与来源信息的 LangChain 文档。
        """
        # text 保存解析器从源文件提取出的完整文本。
        text = ParserFactory.parse(
            self.path,
            self.file_type,
            config=self.config,
        )
        # metadata 记录文件来源字段并合并调用方提供的附加元数据。
        metadata = {
            "source": str(self.path),
            "file_name": self.path.name,
            "file_type": self.file_type.lower().lstrip("."),
            "page": None,
            **self.metadata,
        }
        yield Document(page_content=text, metadata=metadata)
