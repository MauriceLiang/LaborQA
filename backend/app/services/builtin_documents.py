# 文件作用：管理项目自带法律文档的发现、导入和导入状态。
"""扫描仓库内置法律文档并计算可用于导入去重的稳定身份信息。"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from app.core.config import Settings, settings

# 内置知识库允许扫描和导入的文件扩展名集合。
SUPPORTED_BUILTIN_SUFFIXES = frozenset({".pdf", ".doc", ".docx", ".md", ".txt"})


@dataclass(frozen=True)
class BuiltinDocumentSpec:
    """描述单个内置文档及其稳定来源标识。"""

    source_key: str  # 由相对路径构造的稳定去重键。
    file_name: str  # 文档原始文件名。
    file_type: str  # 不含点号的小写扩展名。
    source_path: Path  # 仓库中的源文件路径。
    source_hash: str  # 源文件内容的 SHA-256 摘要。


@dataclass(frozen=True)
class BuiltinImportPlan:
    """描述一次内置文档导入批次的总量、已导入和待跟踪文档。"""

    total: int  # 本次扫描发现的内置文档总数。
    already_imported: int  # 内容已导入且无需重新处理的文档数。
    scheduled_ids: tuple[int, ...]  # 已排入导入任务的文档主键。
    processing_ids: tuple[int, ...]  # 当前仍处于导入过程中的文档主键。

    @property
    def tracked_document_ids(self) -> list[int]:
        """返回本批次需要继续跟踪的排队中和处理中记录主键。
        """
        return [*self.scheduled_ids, *self.processing_ids]

    @property
    def all_imported(self) -> bool:
        """判断是否至少发现一份文档且全部已经导入。
        """
        return self.total > 0 and self.already_imported == self.total


def sha256_file(path: Path) -> str:
    """以分块方式计算文件内容的 SHA-256 摘要。

    参数：path 为待计算的文件路径。
    返回：十六进制 SHA-256 字符串。
    """
    # digest 累积源文件全部字节的 SHA-256 校验值。
    digest = sha256()
    # source 以二进制模式打开，避免文本编码影响摘要结果。
    with path.open("rb") as source:
        # block 保存单次读取的文件内容块，控制内存占用。
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def list_builtin_documents(
    config: Settings = settings,
) -> list[BuiltinDocumentSpec]:
    """扫描内置目录并生成排序稳定的文档描述列表。

    参数：config 为包含内置文档目录配置的应用设置。
    返回：符合支持扩展名的内置文档规格列表。
    """
    # 根据 Settings 解析得到的文档根目录。
    root = config.builtin_document_path
    if not root.is_dir():
        return []

    # paths 是按相对路径排序的全部受支持文档文件。
    paths = sorted(
        (
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in SUPPORTED_BUILTIN_SUFFIXES
        ),
        key=lambda path: path.relative_to(root).as_posix(),
    )
    # specs 收集转换后的内置文档规格。
    specs: list[BuiltinDocumentSpec] = []
    # path 表示当前处理的源文件；relative_path 用于生成稳定来源键。
    for path in paths:
        # 局部变量 relative_path：相对于项目或资料根目录的路径。
        relative_path = path.relative_to(root).as_posix()
        specs.append(
            BuiltinDocumentSpec(
                source_key=f"builtin:{relative_path}",
                file_name=path.name,
                file_type=path.suffix.lower().removeprefix("."),
                source_path=path,
                source_hash=sha256_file(path),
            )
        )
    return specs
