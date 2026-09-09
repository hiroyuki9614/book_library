FROM node:22-bookworm AS build
WORKDIR /app
COPY --chown=node:node package.json package-lock.json ./
RUN npm ci
COPY --chown=node:node prisma ./prisma
COPY --chown=node:node prisma.config.ts ./prisma.config.ts
COPY --chown=node:node tsconfig.json ./tsconfig.json
COPY --chown=node:node src ./src
RUN npx prisma generate --generator client
RUN npm run build

FROM build AS migration

FROM node:22-bookworm-slim AS runtime
WORKDIR /app
ENV NODE_ENV=production
COPY --chown=node:node package.json package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force
COPY --chown=node:node --from=build /app/dist ./dist
COPY --chown=node:node --from=build /app/prisma/generated ./prisma/generated
USER node
EXPOSE 3000
CMD ["npm", "start"]
