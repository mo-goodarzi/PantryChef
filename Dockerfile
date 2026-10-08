# PantryChef image: the API (default command) and the Streamlit UI (see docker-compose.yml).
# Data is never baked in: pantry.db, chroma/ and state.db are mounted at /app/data.
ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}

# The locked torch is the CUDA build (~5 GB of NVIDIA libraries); the app only runs the
# small embedding model on CPU, so the image installs the same torch version as a CPU
# build instead. TORCH_VERSION must match uv.lock (checked by tests/test_docker.py).
# An empty TORCH_INDEX skips torch (only for smoke tests that do not embed).
ARG TORCH_VERSION=2.14.0
ARG TORCH_INDEX=https://download.pytorch.org/whl/cpu

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    HF_HOME=/app/data/hf-cache

RUN pip install --no-cache-dir uv==0.8.17

WORKDIR /app
# Dependencies first, so code changes do not reinstall them. Every locked package except
# torch and its CUDA/triton dependencies, then the CPU torch.
COPY pyproject.toml uv.lock .python-version README.md ./
# The uv cache is a build cache mount: it speeds up rebuilds and never lands in the image.
RUN --mount=type=cache,target=/root/.cache/uv \
    sed -n 's/^name = "\(torch\|nvidia-[^"]*\|triton\)"$/--no-install-package \1/p' uv.lock \
        > /opt/uv-skip-gpu \
    && uv sync --locked --no-dev --no-install-project $(cat /opt/uv-skip-gpu) \
    && if [ -n "$TORCH_INDEX" ]; then \
        uv pip install --python /opt/venv/bin/python --index-url "$TORCH_INDEX" \
            "torch==${TORCH_VERSION}"; \
    fi
COPY src ./src
COPY ui ./ui
COPY .streamlit ./.streamlit
COPY scripts ./scripts
# --inexact keeps the CPU torch installed above.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --inexact $(cat /opt/uv-skip-gpu)

# Run as a normal user (uid 1000 usually owns ./data on the host).
RUN useradd --create-home --uid 1000 app && mkdir -p /app/data && chown app /app/data
USER app

EXPOSE 8000 8501
CMD ["uvicorn", "pantry_chef.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
