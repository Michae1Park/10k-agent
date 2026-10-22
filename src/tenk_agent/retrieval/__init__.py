"""V1 retrieval: a question → the chunks that answer it, with no LLM.

  index.py       chunks → database (FTS5 + vectors)
  decompose.py   question → sub-questions, one per company and filing it names
  search.py      per sub-question: keyword + dense → RRF → rerank; merged round-robin → top k
  embeddings.py  the embedder and reranker, local or hosted, behind one interface each

Spec and results: experiments/V1_RETRIEVAL.md. Guide: docs/V1_RETRIEVAL.md.
"""
