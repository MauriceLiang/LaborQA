# 文件作用：验证当前嵌入 Provider 配置及向量生成链路是否可用。
from app.rag.embeddings import LangChainEmbeddingService


def main() -> None:
    """生成一条测试向量并输出嵌入维度，用于人工验证配置。"""
    # vector 保存示例劳动合同查询的嵌入结果。
    vector = LangChainEmbeddingService().embed_query("劳动合同中的工资支付规定")
    print(f"Embedding verification passed: dimension={len(vector)}")


if __name__ == "__main__":
    main()
