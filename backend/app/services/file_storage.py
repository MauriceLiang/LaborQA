# 文件作用：校验上传文件并安全保存、复制和删除本地文件。
"""校验上传内容并安全保存、验证、复制及删除源文档文件。"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO
from uuid import uuid4

from app.core.error_codes import ErrorCode

# 默认单文件上传大小上限，单位为字节。
DEFAULT_MAX_UPLOAD_SIZE_BYTES = 20 * 1024 * 1024
# 各支持扩展名允许的 MIME 类型集合，用于校验请求头。
_MIME_TYPES_BY_SUFFIX = {
    ".pdf": frozenset({"application/pdf"}),
    ".doc": frozenset({"application/msword", "application/vnd.ms-word"}),
    ".docx": frozenset(
        {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
    ),
    ".txt": frozenset({"text/plain"}),
    ".md": frozenset({"text/markdown", "text/x-markdown", "text/plain", "application/octet-stream"}),
}
# 保存上传内容时单次从输入流读取的最大字节数。
_READ_CHUNK_SIZE = 64 * 1024
# 内部复制文件时按扩展名构造的标准 MIME 类型。
_DEFAULT_MIME_TYPES = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".md": "text/markdown",
    ".txt": "text/plain",
}


class FileProcessingError(Exception):
    """上传校验和文档解析失败时使用的基础异常。"""


class UploadValidationError(FileProcessingError):
    """携带业务错误码和 HTTP 状态的上传校验异常。"""

    def __init__(self, message: str, code: ErrorCode, http_status: int) -> None:
        """初始化上传错误及其 API 响应信息。

        参数：message 为错误提示；code 为业务错误码；http_status 为 HTTP 状态码。
        """
        super().__init__(message)
        # 成员变量 code：业务状态、错误或分类代码。
        self.code = code  # 供 API 异常处理器返回的业务错误码。
        # 成员变量 http_status：异常响应使用的 HTTP 状态码。
        self.http_status = http_status  # 供 API 响应使用的 HTTP 状态码。


class UnsupportedFileTypeError(UploadValidationError):
    """上传文件的扩展名不受支持时抛出的校验异常。"""

    def __init__(self) -> None:
        """初始化不支持文件类型异常及其业务错误码。
        """
        super().__init__(
            "仅支持 PDF、DOC、DOCX、MD 和 TXT 文件",
            ErrorCode.UNSUPPORTED_FILE_TYPE,
            400,
        )


class InvalidFileMimeTypeError(UploadValidationError):
    """上传内容的 MIME 类型与文件扩展名不匹配时抛出的异常。"""

    def __init__(self) -> None:
        """初始化 MIME 类型与扩展名不匹配异常。
        """
        super().__init__("文件 MIME 类型与扩展名不匹配", ErrorCode.INVALID_REQUEST, 400)


class FileTooLargeError(UploadValidationError):
    """上传文件字节数超过当前配置上限时抛出的异常。"""

    def __init__(self, max_size_bytes: int) -> None:
        """记录当前上传大小限制并生成对应的校验提示。

        参数：max_size_bytes 为允许的最大文件字节数。
        """
        super().__init__(
            f"文件超过大小限制（{max_size_bytes} 字节）",
            ErrorCode.FILE_TOO_LARGE,
            413,
        )


class EmptyFileError(UploadValidationError):
    """上传文件为空时抛出的校验异常。"""

    def __init__(self) -> None:
        """初始化空文件校验异常及对应业务错误码。
        """
        super().__init__("上传文件为空", ErrorCode.EMPTY_FILE, 400)


class DocumentConverterUnavailable(FileProcessingError):
    """旧版 DOC 文档没有可用解析器或转换后端时抛出的异常。"""


class DocumentParseError(FileProcessingError):
    """源文档无法读取、解码或解析时抛出的异常。"""


@dataclass(frozen=True)
class StoredFile:
    """描述已通过校验并保存到本地的文件。"""

    file_name: str  # 用户提供的原始文件名。
    file_type: str  # 小写且不含点号的扩展名。
    path: Path  # 服务端生成的安全存储路径。
    size_bytes: int  # 实际写入的文件字节数。
    mime_type: str  # 校验通过的 MIME 类型。


class FileStorage:
    """校验上传内容并以随机文件名原子保存到私有目录。"""

    def __init__(
        self,
        upload_dir: str | Path,
        max_size_bytes: int = DEFAULT_MAX_UPLOAD_SIZE_BYTES,
    ) -> None:
        """设置存储目录和单文件大小限制。

        参数：upload_dir 为文件保存目录；max_size_bytes 为最大上传字节数。
        """
        if max_size_bytes <= 0:
            raise ValueError("max_size_bytes must be positive")
        # 成员变量 upload_dir：用户上传文件保存目录。
        self.upload_dir = Path(upload_dir).expanduser().resolve()  # 规范化后的文件存储根目录。
        # 成员变量 max_size_bytes：文件上传允许的最大字节数。
        self.max_size_bytes = max_size_bytes  # 本实例允许保存的最大文件大小。

    def save(
        self,
        source: BinaryIO,
        filename: str | None,
        content_type: str | None,
    ) -> StoredFile:
        """校验文件名和类型后以临时文件加原子替换方式保存上传内容。

        参数：source 为二进制输入流；filename 为客户端文件名；
        content_type 为请求声明的 MIME 类型。
        返回：保存后的文件名、路径、大小和类型信息。
        """
        # 经过路径剥离和扩展名白名单校验的原始名称及扩展名。
        file_name, suffix = self._validated_name(filename)
        # 与扩展名匹配且规范化后的上传 MIME 类型。
        mime_type = self._validated_mime_type(suffix, content_type)

        self.upload_dir.mkdir(parents=True, exist_ok=True)
        # 使用随机标识构造最终文件路径，不将用户文件名用作磁盘路径。
        destination = self.upload_dir / f"{uuid4().hex}{suffix}"
        # 尚未重命名到目标路径的临时文件；用于失败时清理。
        temporary_path: Path | None = None
        # 已从输入流读取并写入临时文件的累计字节数。
        size_bytes = 0

        try:
            # descriptor 是新建临时文件的文件描述符，temporary_name 是其路径。
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".upload-", suffix=".tmp", dir=self.upload_dir
            )
            # 将临时文件名转换为 Path，供提交及异常清理使用。
            temporary_path = Path(temporary_name)
            # output 是以二进制写入方式打开的临时文件流。
            with os.fdopen(descriptor, "wb") as output:
                while True:
                    # remaining 是触及大小上限前还可写入的字节数。
                    remaining = self.max_size_bytes - size_bytes
                    # block 是当前读取块，额外读取 1 字节以检测超限。
                    block = source.read(min(_READ_CHUNK_SIZE, remaining + 1))
                    if not block:
                        break
                    size_bytes += len(block)
                    if size_bytes > self.max_size_bytes:
                        raise FileTooLargeError(self.max_size_bytes)
                    output.write(block)

                if size_bytes == 0:
                    raise EmptyFileError()

                output.flush()
                os.fsync(output.fileno())

            os.replace(temporary_path, destination)
            temporary_path = None
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

        return StoredFile(
            file_name=file_name,
            file_type=suffix[1:],
            path=destination,
            size_bytes=size_bytes,
            mime_type=mime_type,
        )

    def delete(self, path: str | Path) -> None:
        """仅删除存储根目录内的文件，拒绝越界路径。

        参数：path 为绝对路径或相对于上传目录的文件路径。
        """
        # 待删除路径；相对路径随后会锚定到上传目录。
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.upload_dir / candidate
        # 解析父目录后用于确认候选文件不会越出受控存储目录。
        parent = candidate.parent.resolve()
        if not parent.is_relative_to(self.upload_dir):
            raise ValueError("file path must be inside the upload directory")
        candidate.unlink(missing_ok=True)

    def copy_from_path(
        self,
        source_path: str | Path,
        *,
        filename: str,
    ) -> StoredFile:
        """将可信源文件通过标准校验流程复制到上传存储目录。

        参数：source_path 为已有源文件路径；filename 为保存时采用的文件名。
        返回：复制后生成的 StoredFile 描述。
        """
        # source 是待复制的已有源文件路径。
        source = Path(source_path)
        # 校验目标文件名并取得规范扩展名。
        file_name, suffix = self._validated_name(filename)
        if not source.is_file():
            raise FileNotFoundError(source)
        # input_file 是以二进制只读模式打开的源文件流。
        with source.open("rb") as input_file:
            return self.save(
                input_file,
                file_name,
                _DEFAULT_MIME_TYPES[suffix],
            )

    @staticmethod
    def _validated_name(filename: str | None) -> tuple[str, str]:
        """剥离客户端路径并验证文件名及支持的扩展名。

        参数：filename 为客户端传入的原始文件名。
        返回：安全的基础文件名和规范化扩展名。
        """
        # 同时兼容 Windows 和 POSIX 分隔符，仅保留文件名部分。
        basename = (filename or "").replace("\\", "/").rsplit("/", maxsplit=1)[-1]
        # suffix 是小写扩展名，后续用于白名单和 MIME 类型校验。
        suffix = PurePosixPath(basename).suffix.lower()
        if (
            not basename
            or basename in {".", ".."}
            or suffix not in _MIME_TYPES_BY_SUFFIX
        ):
            raise UnsupportedFileTypeError()
        return basename, suffix

    @staticmethod
    def _validated_mime_type(suffix: str, content_type: str | None) -> str:
        """规范化请求 MIME 类型并验证其与文件扩展名相符。

        参数：suffix 为已校验扩展名；content_type 为客户端声明的类型。
        返回：去除参数并转为小写后的 MIME 类型。
        """
        # 去除 charset 等附加参数并统一大小写的 MIME 类型。
        mime_type = (content_type or "").split(";", maxsplit=1)[0].strip().lower()
        if mime_type not in _MIME_TYPES_BY_SUFFIX[suffix]:
            raise InvalidFileMimeTypeError()
        return mime_type
