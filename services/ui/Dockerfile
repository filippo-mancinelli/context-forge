FROM node:20-alpine AS builder

WORKDIR /app

# Force public registry — overrides any .npmrc or inherited token
ENV npm_config_registry=https://registry.npmjs.org/
ENV npm_config__auth=
ENV npm_config__authtoken=

COPY package.json ./
RUN npm install --legacy-peer-deps

COPY . .

ARG VITE_API_URL=http://localhost:8000
ENV VITE_API_URL=$VITE_API_URL

RUN npm run build

FROM nginx:alpine

COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
