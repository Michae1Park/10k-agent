# Experiments

| Version | Topics | Stack | Test data |
|---|---|---|---|
| [V0 · Parsing and chunking](V0_PARSING_CHUNKING.md) | 1. Rule-based baseline<br>2. Library splitters<br>3. Semantic chunking<br>4. Agentic chunking<br>5. LLM-assisted structural chunking | 1. BeautifulSoup + lxml<br>2. LangChain, LlamaIndex, Unstructured, Docling<br>3. LlamaIndex / LangChain + Qwen3-Embedding<br>4. Qwen3.6-27B + Chonkie<br>5. Qwen3.6-27B + custom rules | [AAPL FY2025 excerpt](V0_testdata/aapl-fy25-excerpt.html) (Items 7, 7A, 8 complete; 41 tables) |
