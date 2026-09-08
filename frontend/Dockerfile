FROM node:22-bookworm AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG VITE_API_BASE_URL
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN test -n "$VITE_API_BASE_URL"
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine AS runtime
COPY --from=build /app/dist /usr/share/nginx/html
RUN printf '%s\n' 'server {' '    listen 8080;' '    server_name _;' '    root /usr/share/nginx/html;' '    location / {' '        try_files $uri $uri/ /index.html;' '    }' '}' > /etc/nginx/conf.d/default.conf
EXPOSE 8080
