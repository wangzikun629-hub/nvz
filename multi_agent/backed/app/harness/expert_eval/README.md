# Expert Bioinformatics Evaluation

`cases.jsonl` contains expert-labelled regression cases for scientific semantics,
evidence traceability, causal support labels and concise answers.

Run an OpenAI-compatible model comparison:

```powershell
.\venv\Scripts\python.exe -m multi_agent.backed.app.harness.model_comparison `
  --models-config multi_agent/backed/app/harness/expert_eval/models.example.json
```

API keys are read from the environment variables named by `api_key_env`.
Use `--responses` with a JSON fixture to evaluate saved answers without network
access.
