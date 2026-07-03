FROM node:22-bookworm-slim

WORKDIR /app/frontend

COPY frontend/package*.json /app/frontend/
RUN npm ci

COPY frontend /app/frontend

ARG NEXT_PUBLIC_API_BASE=http://127.0.0.1:8000
ENV NEXT_PUBLIC_API_BASE=${NEXT_PUBLIC_API_BASE}
ENV NODE_ENV=production

RUN npm run build

EXPOSE 3000

CMD ["npm", "run", "start", "--", "--hostname", "0.0.0.0", "--port", "3000"]
