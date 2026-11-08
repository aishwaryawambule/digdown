# One image: the backend, with the dashboard built into it.
FROM node:22-alpine AS dashboard
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /src
COPY pyproject.toml ./
COPY digdown ./digdown
COPY --from=dashboard /web/dist ./digdown/static
RUN pip install --no-cache-dir .

# Settings, the database and any generated token live on one volume.
WORKDIR /data
ENV DIGDOWN_CONFIG=/data/digdown.yml \
    DIGDOWN_DB_PATH=/data/digdown.db \
    DIGDOWN_HOST=0.0.0.0 \
    DIGDOWN_IN_CONTAINER=1

EXPOSE 8088
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8088/api/health', timeout=2)"
CMD ["digdown", "start"]
