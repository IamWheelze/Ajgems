# Built React app served by Caddy, which also terminates HTTPS (automatic certificates for DOMAIN) and proxies
# /api and /admin to the backend. Build from the repository root:
#   docker build -f web/frontend/Dockerfile -t minefoot-web .
FROM node:22-slim AS build
WORKDIR /src
COPY web/frontend/package.json web/frontend/package-lock.json ./
RUN npm ci
COPY web/frontend ./
RUN npm run build

FROM caddy:2.8
COPY --from=build /src/dist /srv/app
COPY web/Caddyfile /etc/caddy/Caddyfile
