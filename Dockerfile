FROM node:20-bookworm-slim AS frontend
WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY index.html postcss.config.js tailwind.config.ts tsconfig.json tsconfig.node.json vite.config.ts ./
COPY public ./public
COPY src ./src
RUN npm run build

FROM python:3.11-slim AS runtime
WORKDIR /app

LABEL org.opencontainers.image.title="AutoOutlook" \
      org.opencontainers.image.description="Automated severe-weather risk intelligence from HRRR data with SPC-style outlook artifacts and a public React dashboard." \
      org.opencontainers.image.source="https://github.com/ShianMike/AutoOutlook" \
      org.opencontainers.image.url="https://autooutlook.tech" \
      org.opencontainers.image.licenses="NOASSERTION"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AUTOOUTLOOK_HOST=0.0.0.0 \
    PORT=8080

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend ./backend
COPY --from=frontend /app/dist ./dist

EXPOSE 8080
CMD ["sh", "-c", "gunicorn backend.server:app --bind 0.0.0.0:${PORT:-8080} --workers ${WEB_CONCURRENCY:-1} --threads ${WEB_THREADS:-8} --timeout ${GUNICORN_TIMEOUT:-120} --graceful-timeout 30 --access-logfile - --error-logfile -"]
