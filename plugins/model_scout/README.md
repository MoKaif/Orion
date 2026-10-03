# Model Scout

Model Scout measures model fitness; it does not manage packages. Its weekly pass refreshes the
pinned candidates from their official Hugging Face records. Its monthly pass benchmarks Orion's
current rules, every configured Ollama candidate already installed, and Laya when its local HTTP
service is available. Recommendations go to the Review Inbox.

No pass downloads a model or changes routing. Approving a trial records intent only. Approving a
measured model switch writes the gitignored `config/models.local.json` after making a timestamped
backup, and only if the candidate is still installed.

## Optional Laya trial

Keep Laya in its own compatible virtual environment or container so its Torch/Transformers stack
does not enlarge Orion's runtime. Orion itself listens on port 8000, so run Laya on 8765:

```bash
python3.13 -m venv .venv-laya
.venv-laya/bin/pip install "laya[serve]"
LAYA_HOST=127.0.0.1 LAYA_PORT=8765 LAYA_DEVICE=cpu LAYA_PRELOAD=1 \
  LAYA_MODELS=english LAYA_THREADS=4 .venv-laya/bin/laya-serve
```

Then run **Benchmark installed models** on the Model Scout agent page. Change
`config/model_scout.local.json` if the service uses another address. Laya is considered only for
the bounded System-1 decisions in `evals.py`; it is not a replacement for Orion's generative chat
or extraction model.
