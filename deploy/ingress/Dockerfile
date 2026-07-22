# syntax=docker/dockerfile:1.7

ARG PYTHON_IMAGE=python:3.12.13-slim-bookworm@sha256:8a7e7cc04fd3e2bd787f7f24e22d5d119aa590d429b50c95dfe12b3abe52f48b
ARG NGINX_IMAGE=nginxinc/nginx-unprivileged:1.28.0-alpine3.21@sha256:c97ff0bf7cbae369953c6da1232ec14ad9f971d66360c5698db0856a4cd657a0

FROM ${PYTHON_IMAGE} AS renderer
ARG DEMO_HOST
WORKDIR /render
COPY render_config.py nginx.conf.template ./
RUN python render_config.py --host "${DEMO_HOST}" --template nginx.conf.template --output nginx.conf

FROM ${NGINX_IMAGE} AS runtime
COPY --from=renderer --chown=101:101 /render/nginx.conf /etc/nginx/nginx.conf
USER 101:101
EXPOSE 8080 8443
ENTRYPOINT ["nginx", "-g", "daemon off;"]
