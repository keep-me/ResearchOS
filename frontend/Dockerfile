# ============================================================
# ResearchOS Frontend Dockerfile - Nginx 托管静态文件
# ============================================================

ARG BASE_REGISTRY=docker.io

# Stage 1: 安装依赖
FROM ${BASE_REGISTRY}/library/node:20-slim AS deps

WORKDIR /app

# 复制 package 文件
COPY package*.json ./

# 默认官方 npm 源；如需加速可在构建时覆盖 NPM_REGISTRY
ARG NPM_REGISTRY=https://registry.npmjs.org
RUN if [ -f package-lock.json ]; then npm ci --no-audit --no-fund --registry=${NPM_REGISTRY}; else npm install --no-audit --no-fund --registry=${NPM_REGISTRY}; fi

# ============================================================
# Stage 2: 开发模式（Vite HMR）
# ============================================================
FROM deps AS dev

WORKDIR /app
COPY . ./

EXPOSE 3002

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "3002"]

# ============================================================
# Stage 3: 构建生产前端
# ============================================================
FROM deps AS builder

WORKDIR /app
COPY . ./

ENV NODE_OPTIONS=--max-old-space-size=4096
RUN npm run build

# ============================================================
# Stage 2: Nginx 托管
# ============================================================
FROM ${BASE_REGISTRY}/library/nginx:alpine

# 复制自定义 Nginx 配置（已包含 .mjs MIME type）
COPY nginx.conf /etc/nginx/conf.d/default.conf

# 复制构建产物
COPY --from=builder /app/dist /usr/share/nginx/html

# 暴露 80 端口
EXPOSE 80

# Nginx 健康检查
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -sf http://localhost:80 || exit 1

# 启动 Nginx（前台模式）
CMD ["nginx", "-g", "daemon off;"]
