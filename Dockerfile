# ── kitegen demo backend ────────────────────────────────────────────────────
# Multi-stage build: compile frontend → install Python deps → run FastAPI
# with prebuilt static files served under /static.

FROM node:20-slim AS frontend
WORKDIR /app/demo/frontend
COPY demo/frontend/package*.json ./
RUN npm ci
COPY demo/frontend ./
RUN npm run build

FROM python:3.11-slim AS backend

# Set timezone to Beijing (trading hours logic depends on local time)
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

WORKDIR /app

# pyproject.toml editable install needs README.md and the src directory,
# so all three must be copied together.
COPY pyproject.toml README.md ./
COPY src ./src
COPY demo/requirements.txt ./demo/requirements.txt
RUN pip install --no-cache-dir -e . && \
    pip install --no-cache-dir -r demo/requirements.txt

# Copy the rest of the demo source (requirements.txt is already in place)
COPY demo ./demo

# Put the frontend build where docker-compose mounts it for Nginx
RUN mkdir -p /app/demo/static
COPY --from=frontend /app/demo/frontend/dist /app/demo/frontend/dist

# Data directory (mounted to the host by docker-compose for persistence)
RUN mkdir -p /app/demo/data/paper

ENV PYTHONPATH=/app
EXPOSE 8000

# Default start: backend API + monitor + paper trader
CMD ["python", "-m", "uvicorn", "demo.backend:app", "--host", "0.0.0.0", "--port", "8000"]
