FROM node:22-bookworm AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY prisma ./prisma
COPY tsconfig.json ./tsconfig.json
COPY src ./src
RUN npx prisma generate
RUN npm run build

FROM build AS migration

FROM node:22-bookworm-slim AS runtime
WORKDIR /app
ENV NODE_ENV=production
COPY package.json package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force
COPY --from=build /app/dist ./dist
COPY --from=build /app/prisma/generated ./prisma/generated
USER node
EXPOSE 3000
CMD ["npm", "start"]
