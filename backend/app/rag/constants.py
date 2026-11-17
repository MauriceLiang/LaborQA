# 文件作用：集中记录文本切分器版本及可兼容的历史版本标识。
# 当前生产环境使用的法律文本切分算法版本。
CURRENT_SPLITTER_VERSION = "legal-text-splitter-v1"
# 旧版切分算法标识，用于识别既有索引元数据。
LEGACY_SPLITTER_VERSION = "legacy-text-chunker-v1"

# 第 4 批次改造保留旧版边界算法，并通过 LangChain splitter 接口对外提供；
# 调整算法时需同步更新版本常量和兼容集合，避免旧索引被错误识别为可复用。
# 可直接读取或继续兼容的切分器版本集合。
COMPATIBLE_SPLITTER_VERSIONS = frozenset(
    {CURRENT_SPLITTER_VERSION, LEGACY_SPLITTER_VERSION}
)
