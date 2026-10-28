# AquaCreds web (Next.js standalone server). Multi-arch (linux/arm64 for the Oracle VM).
# Build from the repo root:  docker build -f apps/web/Dockerfile -t aquacreds-web .
ARG NODE_IMAGE=node:26.9.0-alpine

FROM ${NODE_IMAGE} AS build
WORKDIR /src
ENV NEXT_TELEMETRY_DISABLED=1 PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
RUN npm install -g pnpm@12.14.0
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml tsconfig.base.json ./
COPY packages/api-client/package.json packages/api-client/
COPY apps/web/package.json apps/web/
COPY tools/openapi-codegen/package.json tools/openapi-codegen/
RUN pnpm install --frozen-lockfile --filter @aquacreds/web...
COPY packages/api-client packages/api-client
COPY apps/web apps/web
RUN pnpm --filter @aquacreds/web run build

FROM ${NODE_IMAGE} AS runtime
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
COPY --from=build --chown=node:node /src/apps/web/.next/standalone ./
COPY --from=build --chown=node:node /src/apps/web/.next/static ./apps/web/.next/static
COPY --from=build --chown=node:node /src/apps/web/public ./apps/web/public
USER node
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:3000/ || exit 1
CMD ["node", "apps/web/server.js"]
