FROM node:20-alpine AS build
WORKDIR /app
COPY frontend/package.json deploy/frontend.yarn.loc[k] ./
RUN { [ -f frontend.yarn.lock ] && mv frontend.yarn.lock yarn.lock && yarn install --frozen-lockfile --network-timeout 600000; } \
 || yarn install --network-timeout 600000
COPY frontend/ .
ENV REACT_APP_BACKEND_URL="" DISABLE_EMERGENT_OVERLAY=true GENERATE_SOURCEMAP=false CI=false NODE_OPTIONS=--max-old-space-size=1536
RUN yarn build

FROM caddy:2-alpine
COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/build /srv
