# 前端只构建静态文件，与目标架构无关，固定在构建机架构上运行以避免多架构构建时走 QEMU 模拟
FROM --platform=$BUILDPLATFORM node:22-slim AS web
WORKDIR /src
# 项目 .npmrc 使用国内镜像，CI 在海外构建时改用官方源
ARG NPM_REGISTRY=https://registry.npmjs.org/
ENV npm_config_registry=$NPM_REGISTRY
RUN npm install -g pnpm@10.28.2
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml .npmrc ./
COPY packages ./packages
RUN pnpm install --frozen-lockfile --ignore-scripts
COPY . .
RUN pnpm build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai
# tzdata：定时采集按本地时间触发；dejavu：登录图形验证码字体
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY --from=web /src/dist /app/dist

ARG APP_VERSION=dev
ARG APP_REPO=
ENV APP_VERSION=$APP_VERSION \
    APP_REPO=$APP_REPO

VOLUME /app/data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"
# 采集、查询、监控任务都在进程内调度，只能单进程运行
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
