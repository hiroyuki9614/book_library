FROM node:22-bookworm AS build
WORKDIR /app
COPY --chown=node:node package.json package-lock.json ./
RUN npm ci
COPY --chown=node:node prisma ./prisma
COPY --chown=node:node scripts ./scripts
COPY --chown=node:node prisma.config.ts ./prisma.config.ts
COPY --chown=node:node tsconfig.json ./tsconfig.json
COPY --chown=node:node src ./src
RUN npx prisma generate --generator client
RUN npm run build

FROM build AS migration

FROM node:22-bookworm-slim AS runtime
WORKDIR /app
ENV NODE_ENV=production
ARG VCS_REF
RUN node -e 'if (!/^[0-9a-f]{40}$/.test(process.argv[1] || "")) process.exit(1); require("node:fs").writeFileSync("/app/build-revision.txt", process.argv[1] + "\n", {mode: 0o444})' "$VCS_REF"
LABEL org.opencontainers.image.revision=$VCS_REF
COPY --chown=node:node package.json package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force
COPY --from=build /app/dist ./dist
COPY --from=build /app/prisma/generated ./prisma/generated
USER node
EXPOSE 3000
CMD ["npm", "start"]
