# 前端 Dockerfile：多阶段，nginx 提供静态文件 + 反代 /api 到 backend。
#
# ponytail: nginx 反代配置见 deploy/nginx.conf

# === build ===
FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

# === runtime: nginx 静态托管 ===
FROM nginx:alpine

# 默认 nginx.conf 替换为我们的（反代到 backend）
COPY nginx.conf /etc/nginx/conf.d/default.conf

# SPA 静态文件
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80

# 健康检查（用 livenessProbe 路径，但 frontend 没有，落在 nginx 默认）
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -q -O- http://localhost/ >/dev/null || exit 1
