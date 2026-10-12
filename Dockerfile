# The review app (Milestone 6). Runs migrations, then Streamlit on port 8501.
ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}

RUN pip install --no-cache-dir uv==0.11.32
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH=/app/.venv/bin:$PATH

# Dependencies first (cached layer), without the generator and dev groups.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-default-groups --no-install-project

COPY alembic.ini ./
COPY migrations ./migrations
COPY src ./src
COPY app ./app
COPY .streamlit ./.streamlit
RUN uv sync --frozen --no-default-groups

RUN useradd --create-home reviewer && chown -R reviewer /app
USER reviewer
EXPOSE 8501
HEALTHCHECK --interval=10s --timeout=3s --retries=6 CMD python -c \
    "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')"
CMD ["sh", "-c", "python -m pipeline.run db upgrade && streamlit run app/streamlit_app.py --server.address=0.0.0.0 --server.port=8501"]
