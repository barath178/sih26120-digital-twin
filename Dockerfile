# Single image: builds the React UI, then serves UI + API + WebSocket from FastAPI on :8000
FROM node:24-slim AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install -r requirements.txt \
 && pip install torch==2.14.0 --index-url https://download.pytorch.org/whl/cpu
COPY backend/ ./
COPY --from=ui /ui/dist /app/frontend/dist
# train models at build time so containers start fast (skips any already present in backend/models)
RUN python -m app.train
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
