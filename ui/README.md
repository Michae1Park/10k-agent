# era UI

A local web UI over V4: **Chat** (Ask, Research or Investigate per message, with an as-of date) and **Reports** (run the S1–S11 workflow, read the note, download the workbook, approve a version).

```bash
export ERA_COMPANIES=companies.yaml
uv sync --extra web --extra local
scripts/serve_vllm.sh qwen3.6-27b-fp8      # the model server (:8001)
uv run era serve                          # the API on :8000
cd ui && npm install && npm run dev        # the UI on :5173 (proxies /api)
```

`npm run build` writes `ui/dist`, which `era serve` then serves at http://localhost:8000. Runs go one at a time (one GPU); chats are saved in `output/web/chats/`.
