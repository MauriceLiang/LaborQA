# 文件作用：创建并检查聊天模型、嵌入模型等外部 Provider。
"""创建兼容的聊天模型与嵌入模型，并封装 Provider 特定参数。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Literal

import httpx
from langchain_core.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import SecretStr

from app.core.config import Settings, settings

# 聊天模型思考模式允许的配置值。
ThinkingMode = Literal["default", "enabled", "disabled"]
# 远程模型支持的推理强度选项。
ReasoningEffort = Literal["low", "high", "max"]


def build_chat_model(
    config: Settings = settings,
    *,
    thinking_mode: ThinkingMode = "default",
    reasoning_effort: ReasoningEffort | None = None,
) -> BaseChatModel:
    """根据应用配置构造 OpenAI 兼容的 LangChain 聊天模型。

    参数：config 为模型连接配置；thinking_mode 和 reasoning_effort 控制推理参数。
    返回：可用于同步或流式生成的 LangChain 聊天模型。
    """
    from langchain_openai import ChatOpenAI

    # kwargs 收集传递给 ChatOpenAI 的连接、模型和流式调用参数。
    kwargs: dict[str, Any] = {
        "model": config.llm_model,
        "api_key": config.llm_api_key,
        "base_url": _without_endpoint_suffix(
            config.llm_base_url, "/chat/completions"
        ),
        "streaming": True,
        "use_responses_api": False,
    }
    if thinking_mode != "default":
        kwargs.update(
            _deepseek_thinking_options(
                thinking_mode=thinking_mode,
                reasoning_effort=reasoning_effort,
            )
        )
    return ChatOpenAI(**kwargs)


def _deepseek_thinking_options(
    *,
    thinking_mode: ThinkingMode,
    reasoning_effort: ReasoningEffort | None = None,
) -> dict[str, Any]:
    """把产品推理设置转换为 DeepSeek 兼容的模型请求参数。

    参数：thinking_mode 为思考开关；reasoning_effort 为可选推理强度。
    返回：需要合并到聊天模型初始化参数中的字典。
    """
    if thinking_mode == "disabled":
        return {
            "reasoning_effort": "none",
            "extra_body": {"thinking": {"type": "disabled"}},
        }
    if thinking_mode == "enabled":
        return {
            "reasoning_effort": reasoning_effort or "high",
            "extra_body": {"thinking": {"type": "enabled"}},
        }
    return {}


def build_embeddings(
    config: Settings = settings,
    *,
    http_client: Any | None = None,
) -> Embeddings:
    """按配置创建本地或远程的 LangChain 文本嵌入实现。

    参数：config 为嵌入 Provider 设置；http_client 为可选复用 HTTP 客户端。
    返回：实现 LangChain Embeddings 接口的嵌入服务。
    """
    if config.embedding_provider == "local":
        from langchain_huggingface import HuggingFaceEmbeddings

        _configure_local_torch_runtime()
        return HuggingFaceEmbeddings(
            model_name=_resolve_local_model(config.local_embedding_model),
            model_kwargs={
                "device": _resolve_local_device(config.local_embedding_device)
            },
            encode_kwargs={
                "normalize_embeddings": config.embedding_normalize,
                "batch_size": config.embedding_batch_size,
            },
        )

    return CompatibleEmbeddings(
        model=config.embedding_api_model,
        api_key=config.embedding_api_key,
        base_url=config.embedding_base_url,
        http_client=http_client,
    )


def _resolve_local_model(model_name: str) -> str:
    """优先使用本地模型路径，否则查找 Hugging Face 本地缓存。

    参数：model_name 为模型名称或本地目录。
    返回：可供模型加载器读取的本地路径或原模型名称。
    """
    # model_name 展开用户目录后对应的本地路径候选。
    local_path = Path(model_name).expanduser()
    if local_path.exists():
        return str(local_path)

    try:
        from huggingface_hub import snapshot_download
        from huggingface_hub.errors import LocalEntryNotFoundError

        return snapshot_download(model_name, local_files_only=True)
    except (LocalEntryNotFoundError, OSError):
        return model_name


def local_embedding_is_cached(config: Settings = settings) -> bool:
    """检查当前配置的本地嵌入模型是否已在本机缓存。

    参数：config 为包含本地模型名称的应用配置。
    返回：模型可在本机离线加载时为 true。
    """
    try:
        from huggingface_hub import snapshot_download
        from huggingface_hub.errors import LocalEntryNotFoundError

        # 当前设置指定的本地嵌入模型名称或路径。
        model_name = config.local_embedding_model
        # 展开用户目录后的本地模型路径候选。
        local_path = Path(model_name).expanduser()
        if local_path.exists():
            return True
        snapshot_download(model_name, local_files_only=True)
        return True
    except (ImportError, LocalEntryNotFoundError, OSError):
        return False


def _resolve_local_device(device: str) -> str:
    """将 auto 设备设置解析为当前机器可用的 CUDA、MPS 或 CPU。

    参数：device 为用户配置的设备值。
    返回：模型加载器可识别的设备标识。
    """
    if device != "auto":
        return device

    import torch

    if torch.cuda.is_available():
        return "cuda"
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _configure_local_torch_runtime() -> None:
    """应用 macOS 下避免 PyTorch 与 FAISS OpenMP 冲突的线程配置。
    """
    if sys.platform != "darwin":
        return

    import torch

    # macOS 下 PyTorch 与 faiss-cpu 可能分别加载不兼容的 OpenMP 运行时；
    # 将 PyTorch 推理限制为单线程，可避免两套原生线程池并发时崩溃或破坏进程状态。
    torch.set_num_threads(1)


class CompatibleEmbeddings(Embeddings):
    """直接向 OpenAI 兼容嵌入端点提交文本并返回向量。

    不依赖模型专属分词器，兼容不支持新版 SDK 扩展参数的 Provider。
    """

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str,
        http_client: httpx.Client | None = None,
    ) -> None:
        """保存远程嵌入请求所需的模型、凭证、端点和可选客户端。

        参数：model 为嵌入模型名；api_key 为密钥；base_url 为服务地址；
        http_client 为可选复用的 HTTP 客户端。
        """
        # 成员变量 model：当前使用的模型或模型名称。
        self.model = model  # 远程嵌入 API 的模型名称。
        # 成员变量 openai_api_key：远程 OpenAI 兼容服务的鉴权密钥。
        self.openai_api_key = SecretStr(api_key)  # 隐藏显示的嵌入 API 密钥。
        # 成员变量 openai_api_base：远程 OpenAI 兼容服务的 API 基础地址。
        self.openai_api_base = _without_endpoint_suffix(base_url, "/embeddings")  # 去除重复 endpoint 后的 API 根地址。
        # 成员变量 _client：复用的模型或 HTTP 请求客户端。
        self._client = http_client  # 注入的 HTTP 客户端；为空时使用 httpx.post。

    @property
    def endpoint(self) -> str:
        """返回最终嵌入请求 URL。
        """
        return f"{self.openai_api_base}/embeddings"

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """批量请求文本向量并按原始输入顺序返回。

        参数：texts 为需要转换为向量的文本列表。
        返回：与输入一一对应的浮点向量列表。
        """
        # post 选择注入客户端的方法或 httpx 的全局 POST 函数。
        post = self._client.post if self._client is not None else httpx.post
        # response 是远程嵌入服务返回的 HTTP 响应。
        response = post(
            self.endpoint,
            headers={
                "Authorization": f"Bearer {self.openai_api_key.get_secret_value()}"
            },
            json={"model": self.model, "input": texts},
            timeout=60.0,
        )
        response.raise_for_status()
        # payload 是服务端返回的 JSON 对象。
        payload = response.json()
        # data 保存响应中的向量记录列表。
        data = payload["data"]
        if not isinstance(data, list) or len(data) != len(texts):
            raise ValueError("embedding response item count does not match request")
        # ordered 按响应索引恢复为与输入文本相同的顺序。
        ordered = sorted(data, key=lambda item: item.get("index", 0))
        # 局部变量 item：当前正在映射的一条业务记录；下方代码会提取其标识、状态或内容字段，组装成返回列表中的一个元素。
        return [item["embedding"] for item in ordered]

    def embed_query(self, text: str) -> list[float]:
        """将单条查询文本转换为向量。

        参数：text 为需要嵌入的查询文本。
        返回：查询对应的浮点向量。
        """
        return self.embed_documents([text])[0]


def _without_endpoint_suffix(base_url: str, suffix: str) -> str:
    """规范化基础 URL，并移除已存在的指定 endpoint 后缀。

    参数：base_url 为配置的服务地址；suffix 为需要移除的 endpoint 路径。
    返回：不带末尾斜杠和重复 endpoint 的基础 URL。
    """
    # 去掉末尾斜杠后的服务 URL。
    normalized = base_url.rstrip("/")
    return normalized.removesuffix(suffix)
