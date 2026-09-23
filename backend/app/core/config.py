# 文件作用：集中声明环境变量配置、默认值、校验规则及项目路径解析。
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录，用于解析相对配置路径及仓库级 .env 文件。
PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """应用运行配置；字段可由对应的大写环境变量覆盖。"""

    # Pydantic Settings 的环境文件位置、编码和未知字段处理策略。
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "劳动权益咨询问答台"  # API 文档及服务展示使用的应用名称。
    api_prefix: str = "/api"  # 所有业务 API 路由的统一前缀。
    cors_origins: list[str] = ["http://127.0.0.1:5173", "http://localhost:5173"]  # 允许跨域访问的前端源地址。
    log_level: str = "INFO"  # Python 日志系统的最低输出级别。
    database_url: str = "sqlite:///./data/app.db"  # SQLite 数据库连接 URL。
    upload_dir: Path = Path("./uploads")  # 用户上传原始文件的存储目录。
    builtin_document_dir: Path = Path("./reference-data/documents")  # 项目内置劳动法律资料目录。
    faiss_dir: Path = Path("./data/faiss")  # FAISS 向量索引的持久化目录。
    max_upload_size_mb: int = Field(default=20, ge=1)  # 单个上传文件允许的最大 MB 数。
    doc_parser_backend: Literal["package", "libreoffice"] = "package"  # DOC 文件解析或转换后端。
    doc_converter: str = "libreoffice"  # LibreOffice 可执行程序名称或路径。
    chunk_size: int = Field(default=600, ge=100, le=2000)  # 文档切分时单个文本块的目标长度。
    chunk_overlap: int = Field(default=100, ge=0, le=500)  # 相邻文本块之间保留的重叠长度。
    llm_api_key: str = ""  # 大语言模型服务鉴权密钥。
    llm_base_url: str = ""  # 大语言模型兼容 API 的基础地址。
    llm_model: str = ""  # 用于回答生成及相关任务的模型名称。
    llm_rewrite_temperature: float = Field(default=0.1, ge=0.0, le=2.0)  # 查询改写阶段的采样温度。
    llm_rewrite_top_p: float = Field(default=1.0, ge=0.0, le=1.0)  # 查询改写阶段的 nucleus 采样阈值。
    llm_rewrite_max_tokens: int = Field(default=512, ge=1)  # 查询改写阶段允许生成的最大 token 数。
    llm_evidence_temperature: float = Field(default=0.0, ge=0.0, le=2.0)  # 证据判断阶段的采样温度。
    llm_evidence_top_p: float = Field(default=1.0, ge=0.0, le=1.0)  # 证据判断阶段的 nucleus 采样阈值。
    llm_evidence_max_tokens: int = Field(default=1024, ge=1)  # 证据判断阶段允许生成的最大 token 数。
    llm_answer_temperature: float = Field(default=0.2, ge=0.0, le=2.0)  # 最终答案生成阶段的采样温度。
    llm_answer_top_p: float = Field(default=1.0, ge=0.0, le=1.0)  # 最终答案生成阶段的 nucleus 采样阈值。
    llm_answer_max_tokens: int = Field(default=4096, ge=1)  # 最终答案生成阶段允许生成的最大 token 数。
    rag_candidate_k: int = Field(default=20, ge=5, le=50)  # 向量检索阶段初筛的候选文本块数量。
    rag_top_k: int = Field(default=5, ge=1, le=20)  # 重排后供回答流程使用的主要结果数量。
    rag_score_threshold: float = Field(default=0.35, ge=0, le=1)  # 低于该相似度阈值的候选片段不作为有效检索结果。
    lightweight_rerank_enabled: bool = True  # 是否启用内置轻量级重排。
    rag_neighbor_expand_top_n: int = Field(default=3, ge=0, le=10)  # 对排名靠前的结果扩展相邻片段的最多锚点数。
    rag_neighbor_radius: int = Field(default=1, ge=0, le=2)  # 每个锚点向前、向后扩展的相邻片段距离。
    rag_evidence_limit: int = Field(default=10, ge=5, le=20)  # 送入证据判断阶段的片段数量上限。
    rerank_enabled: bool = False  # 是否启用配置的重排 Provider。
    rerank_top_n: int = Field(default=5, ge=1, le=20)  # 重排后保留的候选片段数。
    default_answer_style: Literal["plain", "legal"] = "plain"  # 未指定时采用的答案表达风格。
    embedding_provider: Literal["local", "api"] = "local"  # 选择本地嵌入模型或远程嵌入 API。
    local_embedding_model: str = "BAAI/bge-small-zh-v1.5"  # 本地中文文本向量模型标识或缓存路径。
    local_embedding_device: Literal["auto", "cpu", "cuda", "mps"] = "auto"  # 本地嵌入模型使用的计算设备。
    embedding_normalize: bool = True  # 是否对生成的向量执行归一化。
    embedding_batch_size: int = 32  # 本地嵌入模型每批处理的文本数量。
    embedding_api_key: str = ""  # 远程嵌入服务鉴权密钥。
    embedding_base_url: str = ""  # 远程嵌入 API 的基础地址。
    embedding_api_model: str = ""  # 远程嵌入服务使用的模型名称。

    @model_validator(mode="after")
    def require_api_embedding_settings(self) -> "Settings":
        """校验切分、检索参数之间的约束及 API 嵌入所需配置。

        返回：完成交叉字段校验的当前 Settings 实例；配置无效时抛出 ValueError。
        """
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("CHUNK_OVERLAP must be less than CHUNK_SIZE")
        if self.rag_top_k > self.rag_candidate_k:
            raise ValueError("RAG_TOP_K must be less than or equal to RAG_CANDIDATE_K")
        if self.rag_evidence_limit < self.rag_top_k:
            raise ValueError(
                "RAG_EVIDENCE_LIMIT must be greater than or equal to RAG_TOP_K"
            )
        if self.rag_neighbor_expand_top_n > self.rag_top_k:
            raise ValueError(
                "RAG_NEIGHBOR_EXPAND_TOP_N must be less than or equal to RAG_TOP_K"
            )
        if self.rerank_enabled and self.rerank_top_n > self.rag_candidate_k:
            raise ValueError(
                "RERANK_TOP_N must be less than or equal to RAG_CANDIDATE_K"
            )
        if self.embedding_provider == "api" and not all(
            (self.embedding_api_key, self.embedding_base_url, self.embedding_api_model)
        ):
            raise ValueError(
                "EMBEDDING_API_KEY, EMBEDDING_BASE_URL and EMBEDDING_API_MODEL "
                "are required when EMBEDDING_PROVIDER=api"
            )
        return self

    @property
    def database_path(self) -> Path:
        """将 SQLite URL 解析为数据库文件路径，保留内存数据库标记。
        """
        # SQLite URL 中数据库路径之前的固定 scheme 前缀。
        prefix = "sqlite:///"
        if not self.database_url.startswith(prefix):
            raise ValueError("DATABASE_URL must use SQLite")

        # 去除 scheme 后得到的原始路径或 :memory: 特殊标记。
        raw_path = self.database_url.removeprefix(prefix)
        if raw_path == ":memory:":
            return Path(raw_path)

        # 将 URL 中的路径文本转换为 pathlib 路径对象。
        path = Path(raw_path)
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def embedding_model(self) -> str:
        """返回当前 Provider 实际使用的嵌入模型名称。
        """
        return (
            self.local_embedding_model
            if self.embedding_provider == "local"
            else self.embedding_api_model
        )

    @property
    def faiss_path(self) -> Path:
        """返回相对于项目根目录解析后的 FAISS 索引目录。
        """
        return self._project_path(self.faiss_dir)

    @property
    def upload_path(self) -> Path:
        """返回相对于项目根目录解析后的上传文件目录。
        """
        return self._project_path(self.upload_dir)

    @property
    def builtin_document_path(self) -> Path:
        """返回相对于项目根目录解析后的内置文档目录。
        """
        return self._project_path(self.builtin_document_dir)

    @staticmethod
    def _project_path(path: Path) -> Path:
        """将相对路径锚定到项目根目录，绝对路径保持不变。

        参数：path 为配置中声明的目录路径。
        返回：解析后的绝对路径；输入已是绝对路径时原样返回。
        """
        return path if path.is_absolute() else PROJECT_ROOT / path


# 从环境变量和 .env 文件加载的全局应用配置实例。
settings = Settings()
