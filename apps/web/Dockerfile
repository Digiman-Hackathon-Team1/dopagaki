# 빌드 컨텍스트는 레포 루트다.
FROM node:24-bookworm AS build
RUN npm install -g pnpm@12.3.4
WORKDIR /repo
COPY . .
RUN pnpm install --frozen-lockfile --ignore-scripts --filter @repo/web...

# Railway 서비스 변수는 ARG로 선언해야 빌드에 전달된다.
ARG VITE_POSTHOG_KEY VITE_POSTHOG_HOST VITE_CONCIERGE_API
RUN pnpm --filter @repo/web proofs:pull && pnpm --filter @repo/web build

FROM caddy:2.10-alpine
COPY apps/web/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /repo/apps/web/dist /srv
