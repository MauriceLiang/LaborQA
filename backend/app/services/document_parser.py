# 文件作用：解析 PDF、DOC、DOCX、Markdown 和文本文件并提取正文。
"""从 PDF、Office 文档和纯文本文件中提取可供知识库处理的正文。"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from app.core.config import Settings, settings
from app.services.file_storage import (
    DocumentConverterUnavailable,
    DocumentParseError,
    EmptyFileError,
    UnsupportedFileTypeError,
)

# 调用 LibreOffice 转换 DOC 文件时允许的最长秒数。
_CONVERTER_TIMEOUT_SECONDS = 120


def find_doc_converter(preferred: str | None = None) -> str | None:
    """按优先级查找配置的 DOC 转换器或常见 LibreOffice 命令。

    参数：preferred 为可选的首选可执行文件名或路径。
    返回：找到的可执行文件路径；均不存在时返回 None。
    """
    return (
        shutil.which(preferred or settings.doc_converter)
        or shutil.which("soffice")
        or shutil.which("libreoffice")
    )


def package_doc_parser_available() -> bool:
    """检查纯 Python 的旧版 DOC 转换包是否已安装且可调用。"""
    try:
        from doc2docx import convert
    except ImportError:
        return False
    return callable(convert)


def is_doc_parser_available(config: Settings | None = None) -> bool:
    """检查当前配置指定的 DOC 解析后端或备用转换器是否可用。

    参数：config 为可选应用配置；为空时使用全局配置。
    返回：至少有一个可用 DOC 解析路径时为 true。
    """
    # 当前生效的 DOC 解析后端名称；未配置时回退到 package。
    parser_backend = getattr(config or settings, "doc_parser_backend", "package")
    if parser_backend == "package" and package_doc_parser_available():
        return True
    return (
        find_doc_converter(getattr(config or settings, "doc_converter", None))
        is not None
    )


class ParserFactory:
    """按文件类型分发文档解析，并将格式专属异常统一映射为解析错误。"""

    @classmethod
    def parse(
        cls,
        path: str | Path,
        file_type: str,
        *,
        config: Settings | None = None,
    ) -> str:
        """识别并解析支持的源文件格式，返回提取出的文本。

        参数：path 为源文件路径；file_type 为声明的文件格式；config 为可选设置。
        返回：从文档中提取的纯文本内容。
        """
        # source_path 是用于文件系统访问的标准 Path 对象。
        source_path = Path(path)
        # 兼容枚举或字符串输入并转换为统一的小写扩展名标识。
        normalized_type = str(getattr(file_type, "value", file_type))
        normalized_type = normalized_type.lower().lstrip(".")
        if normalized_type not in {"pdf", "doc", "docx", "md", "txt"}:
            raise UnsupportedFileTypeError()

        # 实际文件路径中的扩展名，用于防止声明格式与文件名冲突。
        suffix = source_path.suffix.lower().lstrip(".")
        if suffix and suffix != normalized_type:
            raise UnsupportedFileTypeError()

        try:
            if source_path.stat().st_size == 0:
                raise EmptyFileError()
        # 局部变量 exc：当前 except 分支捕获的异常实例，保留原始类型和消息；后续据此记录上下文、转换业务错误或通过 raise from 保留异常因果链。
        except OSError as exc:
            # exc 表示文件系统或进程调用层面的转换错误。
            # exc 包含文件状态检查失败的系统错误，转换为统一解析异常。
            raise DocumentParseError("无法读取文档文件") from exc

        if normalized_type == "pdf":
            return cls._parse_pdf(source_path)
        if normalized_type == "docx":
            return cls._parse_docx(source_path)
        if normalized_type == "txt":
            return cls._parse_txt(source_path)
        if normalized_type == "md":
            return cls._parse_markdown(source_path)
        return cls._parse_doc(source_path, config=config)

    @staticmethod
    def _parse_txt(path: Path) -> str:
        """解析 UTF-8 文本文件。参数 path 为待解析文件路径。

        参数：
        path：当前文件或索引所在的路径。
        """
        return ParserFactory._parse_text(path, "TXT")

    @staticmethod
    def _parse_markdown(path: Path) -> str:
        """解析 Markdown 文件并返回其原始文本。参数 path 为文件路径。

        参数：
        path：当前文件或索引所在的路径。
        """
        return ParserFactory._parse_text(path, "MD")

    @staticmethod
    def _parse_text(path: Path, file_label: str) -> str:
        """按 UTF-8 读取纯文本并拒绝空内容。

        参数：path 为文件路径；file_label 为错误提示中使用的格式名称。
        返回：文件正文文本。
        """
        try:
            # text 为去除 UTF-8 BOM 后读取的文件正文。
            text = path.read_text(encoding="utf-8-sig")
        # 局部变量 exc：当前 except 分支捕获的异常实例，保留原始类型和消息；后续据此记录上下文、转换业务错误或通过 raise from 保留异常因果链。
        except UnicodeDecodeError as exc:
            raise DocumentParseError(f"{file_label} 文件不是有效的 UTF-8 文本") from exc
        except OSError as exc:
            raise DocumentParseError(f"无法读取 {file_label} 文件") from exc
        if not text.strip():
            raise EmptyFileError()
        return text

    @staticmethod
    def _parse_pdf(path: Path) -> str:
        """逐页提取 PDF 可复制文本并检查解析结果。参数 path 为 PDF 文件路径。

        参数：
        path：当前文件或索引所在的路径。
        """
        try:
            from pypdf import PdfReader

            # reader 负责读取 PDF 页结构和提取每页文本。
            reader = PdfReader(str(path))
            # 将各页提取文本按换行拼接，保留页间分隔。
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        # 局部变量 exc：当前 except 分支捕获的异常实例，保留原始类型和消息；后续据此记录上下文、转换业务错误或通过 raise from 保留异常因果链。
        except Exception as exc:
            if isinstance(exc, DocumentParseError):
                raise
            raise DocumentParseError("PDF 文件解析失败") from exc
        if not text.strip():
            raise DocumentParseError("PDF 未提取到可复制文本")
        return text

    @staticmethod
    def _parse_docx(path: Path) -> str:
        """按文档顺序提取 DOCX 段落和表格文本。参数 path 为 DOCX 文件路径。

        参数：
        path：当前文件或索引所在的路径。
        """
        try:
            from docx import Document
            from docx.table import Table
            from docx.text.paragraph import Paragraph

            # document 是 python-docx 加载后的文档对象。
            document = Document(str(path))
            # blocks 按文档顺序累积段落和表格行文本。
            blocks: list[str] = []
            # block 表示当前正文段落或表格内容块。
            for block in document.iter_inner_content():
                if isinstance(block, Paragraph):
                    blocks.append(block.text)
                elif isinstance(block, Table):
                    # row 表示当前表格行，单元格内容按制表符拼接。
                    for row in block.rows:
                        # 局部变量 cell：DOCX 表格中的当前单元格。
                        blocks.append("\t".join(cell.text for cell in row.cells))
            # text 为按换行连接后的 DOCX 全文。
            text = "\n".join(blocks)
        # 局部变量 exc：当前 except 分支捕获的异常实例，保留原始类型和消息；后续据此记录上下文、转换业务错误或通过 raise from 保留异常因果链。
        except Exception as exc:
            if isinstance(exc, DocumentParseError):
                raise
            raise DocumentParseError("DOCX 文件解析失败") from exc
        if not text.strip():
            raise EmptyFileError()
        return text

    @classmethod
    def _parse_doc(
        cls,
        path: Path,
        *,
        config: Settings | None = None,
    ) -> str:
        """根据应用配置选择 Python 包或 LibreOffice 解析旧版 DOC。

        参数：path 为 DOC 文件路径；config 为可选应用配置。
        返回：DOC 文件中提取出的正文。
        """
        # 当前 DOC 解析流程使用的设置实例。
        parser_config = config or settings
        if (
            parser_config.doc_parser_backend == "package"
            and package_doc_parser_available()
        ):
            return cls._parse_doc_with_package(path)
        return cls._parse_doc_with_libreoffice(path, parser_config.doc_converter)

    @classmethod
    def _parse_doc_with_package(cls, path: Path) -> str:
        """用 doc2docx 将 DOC 转为 DOCX 后复用 DOCX 解析流程。

        参数：path 为旧版 DOC 文件路径。
        返回：转换后的 DOCX 正文文本。
        """
        try:
            from doc2docx import convert
        # 局部变量 exc：当前 except 分支捕获的异常实例，保留原始类型和消息；后续据此记录上下文、转换业务错误或通过 raise from 保留异常因果链。
        except Exception as exc:
            raise DocumentConverterUnavailable("msdoc2docx 未安装或不可用") from exc

        try:
            # 局部变量 directory：当前文件处理所用的目录路径。
            with tempfile.TemporaryDirectory(
                prefix="laborqa-doc-convert-"
            ) as directory:
                # converted_path 是临时转换结果 DOCX 的目标路径。
                converted_path = Path(directory) / "converted.docx"
                # result 保存转换库的执行报告及诊断信息。
                result = convert(path, converted_path)
                if cls._conversion_report_has_errors(result):
                    raise DocumentParseError("DOC 转换报告包含错误")
                if not converted_path.is_file():
                    raise DocumentParseError("DOC 转换器未生成 DOCX 文件")
                return cls._parse_docx(converted_path)
        except DocumentParseError:
            raise
        except Exception as exc:
            raise DocumentParseError("DOC 转换失败") from exc

    @staticmethod
    def _conversion_report_has_errors(result: object) -> bool:
        """检查 doc2docx 转换报告中是否包含 error 级诊断。

        参数：result 为转换库的返回对象。
        返回：存在错误级诊断时为 true。
        """
        # report 是转换结果附带的可选报告对象。
        report = getattr(result, "report", None)
        # diagnostics 是报告中可能为空的诊断项集合。
        diagnostics = getattr(report, "diagnostics", ())
        # diagnostic 表示一条转换诊断；severity 提取其等级字段。
        for diagnostic in diagnostics:
            # 局部变量 severity：当前诊断问题的严重程度。
            severity = getattr(diagnostic, "severity", diagnostic)
            # 兼容枚举或普通字符串形式的 severity 值。
            severity_value = getattr(severity, "value", severity)
            if str(severity_value).lower() == "error":
                return True
        return False

    @classmethod
    def _parse_doc_with_libreoffice(
        cls, path: Path, preferred: str | None = None
    ) -> str:
        """通过隔离的 LibreOffice 用户配置目录转换并解析 DOC。

        参数：path 为 DOC 文件路径；preferred 为可选转换器路径。
        返回：转换后文档中的正文文本。
        """
        # converter 是实际可执行的 DOC 转换程序路径。
        converter = find_doc_converter(preferred)
        if converter is None:
            raise DocumentConverterUnavailable("DOC 解析器未安装或不可用")

        try:
            # 局部变量 directory：当前文件处理所用的目录路径。
            with tempfile.TemporaryDirectory(
                prefix="laborqa-doc-convert-"
            ) as directory:
                # 临时工作目录，存放 LibreOffice 配置和转换结果。
                temporary_directory = Path(directory)
                # 独立的 LibreOffice 用户配置目录，避免与其他任务冲突。
                user_profile = temporary_directory / "profile"
                user_profile.mkdir()
                # command 是无界面执行的转换命令及其参数列表。
                command = [
                    converter,
                    f"-env:UserInstallation={user_profile.as_uri()}",
                    "--headless",
                    "--convert-to",
                    "docx",
                    "--outdir",
                    str(temporary_directory),
                    str(path),
                ]
                subprocess.run(
                    command,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=_CONVERTER_TIMEOUT_SECONDS,
                )
                # 局部变量 converted_path：文档转换器生成的文本文件路径。
                converted_path = temporary_directory / f"{path.stem}.docx"
                if not converted_path.is_file():
                    raise DocumentParseError("DOC 转换器未生成 DOCX 文件")
                return cls._parse_docx(converted_path)
        except DocumentConverterUnavailable:
            raise
        except DocumentParseError:
            raise
        # 局部变量 exc：当前 except 分支捕获的异常实例，保留原始类型和消息；后续据此记录上下文、转换业务错误或通过 raise from 保留异常因果链。
        except FileNotFoundError as exc:
            # exc 表示转换程序在启动时不可用。
            raise DocumentConverterUnavailable("LibreOffice 不可用") from exc
        except subprocess.TimeoutExpired as exc:
            # exc 表示转换耗时超过允许时限。
            raise DocumentParseError("DOC 转换超时") from exc
        except subprocess.CalledProcessError as exc:
            # exc 包含 LibreOffice 返回的非零退出状态。
            raise DocumentParseError("DOC 转换失败") from exc
        except OSError as exc:
            raise DocumentParseError("DOC 转换失败") from exc
