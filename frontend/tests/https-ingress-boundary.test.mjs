import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const repositoryRoot = new URL("../../", import.meta.url);

async function read(path) {
  return readFile(new URL(path, repositoryRoot), "utf8");
}

test("C10-04 renders one immutable validated Demo hostname into a pinned ingress image", async () => {
  const [dockerfile, renderer] = await Promise.all([
    read("deploy/ingress/Dockerfile"),
    read("deploy/ingress/render_config.py"),
  ]);

  assert.match(
    dockerfile,
    /nginxinc\/nginx-unprivileged:1\.28\.0-alpine3\.21@sha256:[a-f0-9]{64}/u,
  );
  assert.match(dockerfile, /python:3\.12\.13-slim-bookworm@sha256:[a-f0-9]{64}/u);
  assert.match(dockerfile, /ARG DEMO_HOST/u);
  assert.match(dockerfile, /USER 101:101/u);
  assert.match(dockerfile, /ENTRYPOINT \["nginx", "-g", "daemon off;"\]/u);
  assert.match(renderer, /ASCII/u);
  assert.match(renderer, /api\\\./u);
  assert.match(renderer, /fullmatch/u);
  assert.doesNotMatch(renderer, /shell=True|os\.system|subprocess/u);
});

test("C10-04 fixes TLS limits headers and streaming proxy semantics", async () => {
  const template = await read("deploy/ingress/nginx.conf.template");

  for (const directive of [
    "ssl_protocols TLSv1.2 TLSv1.3",
    "client_max_body_size 1m",
    "proxy_connect_timeout 5s",
    "proxy_read_timeout 60s",
    "limit_req_status 429",
    "limit_conn_status 429",
    "proxy_buffering off",
    "proxy_http_version 1.1",
    "proxy_set_header Upgrade $http_upgrade",
    "proxy_set_header X-Forwarded-Proto https",
    "server_tokens off",
    "Strict-Transport-Security",
    "X-Content-Type-Options",
    "Content-Security-Policy",
    "Referrer-Policy",
  ]) {
    assert.ok(template.includes(directive), `${directive} is missing`);
  }
  assert.match(template, /listen 8080 default_server/u);
  assert.match(template, /return 308 https:\/\/__DEMO_HOST__\$request_uri/u);
  assert.match(template, /listen 8443 ssl default_server/u);
  assert.match(template, /return 421/u);
  assert.match(template, /proxy_pass http:\/\/control-plane:8000/u);
  assert.doesNotMatch(template, /proxy_pass\s+\$/u);
});

test("C10-04 verifies real TLS request bounds and rate limiting without public ports", async () => {
  const acceptance = await read("scripts/run_c10_04_acceptance.py");

  assert.match(acceptance, /api\.automation-tool\.test/u);
  assert.match(acceptance, /TLSVersion\.TLSv1_1/u);
  assert.match(acceptance, /HTTPStatus\.REQUEST_ENTITY_TOO_LARGE/u);
  assert.match(acceptance, /HTTPStatus\.TOO_MANY_REQUESTS/u);
  assert.match(acceptance, /HTTPStatus\.MISDIRECTED_REQUEST/u);
  assert.match(acceptance, /strict-transport-security/iu);
  assert.match(acceptance, /PortBindings/u);
  assert.match(acceptance, /read-only/u);
  assert.doesNotMatch(acceptance, /--publish|-p["']/u);
});
