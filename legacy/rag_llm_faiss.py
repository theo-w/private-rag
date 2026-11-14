"""
【历史版本，仅作参考】本地 RAG Demo：Ollama + FAISS + BGE-M3 + Qwen
最初的单脚本实现：每次启动都在内存中重建 FAISS 索引，不支持增量入库和多轮维护。
当前主流程见 scripts/ingest.py（入库）与 scripts/chat_cli.py（检索问答）。
"""

from pathlib import Path

from langchain_community.document_loaders import TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.embeddings import OllamaEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_community.llms import Ollama
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser

# ========== 配置区 ==========
ROOT = Path(__file__).resolve().parent.parent
TXT_FILE = str(ROOT / "data" / "docs" / "test.txt")
CHUNK_SIZE = 800
CHUNK_OVERLAP = 120
TOP_K = 4
EMB_MODEL = "bge-m3"
LLM_MODEL = "qwen3.8:27b-mlx"

# 1. 加载文本
loader = TextLoader(TXT_FILE, encoding="utf-8")
documents = loader.load()

# 2. 文本切分
splitter = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE,
    chunk_overlap=CHUNK_OVERLAP,
    separators=["\n\n", "\n", ". ", " ", ""]
)
chunks = splitter.split_documents(documents)
print(f"✅ 文档切分完成，共 {len(chunks)} 块")

# 3. 向量化 + 构建FAISS向量库
embedding = OllamaEmbeddings(model=EMB_MODEL)
vector_db = FAISS.from_documents(chunks, embedding)
retriever = vector_db.as_retriever(search_kwargs={"k": TOP_K})

# 4. Prompt模板
prompt_template = ChatPromptTemplate.from_template("""
你是文档问答助手，**只允许基于【参考上下文】回答**。
如果上下文没有对应信息，直接回复：【资料中未找到相关内容】，严禁编造、猜测。

【参考上下文】
{context}

用户问题：{question}
""")

# 5. 大模型
llm = Ollama(model=LLM_MODEL)

# 把检索到的文档拼接成文本
def concat_docs(docs):
    return "\n\n".join([doc.page_content for doc in docs])

# 组装RAG链路
rag_chain = (
    {"context": retriever | concat_docs, "question": RunnablePassthrough()}
    | prompt_template
    | llm
    | StrOutputParser()
)

# 交互循环
if __name__ == "__main__":
    print("\n🤖 RAG就绪，输入问题，exit退出")
    while True:
        user_q = input("\n❓ 提问：")
        if user_q.strip().lower() == "exit":
            print("👋 退出")
            break
        answer = rag_chain.invoke(user_q)
        print("\n📝 回答：\n", answer)
