FROM public.ecr.aws/docker/library/node:20-alpine AS builder

WORKDIR /app

# Force public registry — overrides any .npmrc or inherited token
ENV npm_config_registry=https://registry.npmjs.org/
ENV npm_config__auth=
ENV npm_config__authtoken=

COPY package.json ./
RUN npm install --legacy-peer-deps

COPY . .

# Empty by default → the SPA uses same-origin /api paths, which nginx proxies
# to the backend (see default.conf.template). Override only for split-origin setups.
ARG VITE_API_URL=
ENV VITE_API_URL=$VITE_API_URL

RUN npm run build

FROM public.ecr.aws/docker/library/nginx:alpine

COPY --from=builder /app/dist /usr/share/nginx/html
# Placed as a template so nginx's entrypoint renders ${BACKEND_UPSTREAM} at start.
COPY default.conf.template /etc/nginx/templates/default.conf.template

# Where nginx forwards /api/ requests. Compose sets the service name; override on Dokploy.
ENV BACKEND_UPSTREAM=http://context-forge:8000

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
