FROM node:22-bookworm AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG VITE_API_BASE_URL
ARG VITE_PUBLIC_DEMO_EMAIL=""
ARG VITE_PUBLIC_DEMO_PASSWORD=""
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
ENV VITE_PUBLIC_DEMO_EMAIL=${VITE_PUBLIC_DEMO_EMAIL}
ENV VITE_PUBLIC_DEMO_PASSWORD=${VITE_PUBLIC_DEMO_PASSWORD}
RUN test -n "$VITE_API_BASE_URL"
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine AS runtime
ARG VCS_REF
ARG PUBLIC_DEMO_ACCOUNTS_SHA256=""
LABEL org.opencontainers.image.revision=${VCS_REF}
LABEL com.belib.public-demo-accounts-sha256=${PUBLIC_DEMO_ACCOUNTS_SHA256}
COPY --from=build /app/dist /usr/share/nginx/html
USER root
RUN chmod -R a+rX /usr/share/nginx/html
USER 101
RUN printf '%s\n' 'server {' '    listen 8080;' '    server_name _;' '    root /usr/share/nginx/html;' '    location / {' '        try_files $uri $uri/ /index.html;' '    }' '}' > /etc/nginx/conf.d/default.conf
EXPOSE 8080
