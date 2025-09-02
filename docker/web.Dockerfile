# syntax=docker/dockerfile:1

# 1) Сборка фронта
FROM node:22-alpine AS build
WORKDIR /app
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
# если используешь env-переменные для baseURL — подставь здесь, либо через vite env
RUN npm run build

# 2) Nginx для выдачи статики и прокси на API
FROM nginx:1.27-alpine
# конфиг с прокси /api → api:5179
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8501
CMD ["nginx","-g","daemon off;"]
