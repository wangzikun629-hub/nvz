# ── Stage 1: 构建阶段 ─────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /app

# 系统依赖（编译 unstructured / paramiko 等需要）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libffi-dev libssl-dev \
    poppler-utils tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖（仅生产包）
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ── Stage 2: 运行阶段 ─────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

WORKDIR /app

# 运行时系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils tesseract-ocr openssh-client \
    && rm -rf /var/lib/apt/lists/*

# 从构建阶段复制已安装的包
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# 复制项目代码（排除 venv、前端 node_modules 等）
COPY . .

# 创建非 root 用户
RUN useradd -m -u 1001 appuser && \
    mkdir -p /app/multi_agent/backed/app/.project_sftp_cache && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["python", "-m", "multi_agent.backed.app.api.main"]
