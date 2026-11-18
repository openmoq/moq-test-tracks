# Deploying your own service

The whole service (dashboard, API, runner, Prometheus and Grafana, behind
Caddy for TLS) runs on one host with Docker Compose, from
[deploy/compose.yaml](../deploy/compose.yaml). To publish a track from your
own machine without a service, see [Running tracks locally](local.md).

## Requirements

- A Linux host with Docker Engine and the Compose plugin. Docker Desktop does
  not work: node-exporter mounts the host root with `rslave` propagation.
- Linux 6.8 or later. Older kernels have no per-namespace
  `net.core.optmem_max`, and the runner fails to start; delete that line from
  `deploy/compose.yaml` on them.
- 8 CPUs and 8 GB for the runner (`cpus` and `mem_limit` in
  `deploy/compose.yaml`). Docker refuses a container that asks for more CPUs
  than the host has, so lower them on a smaller host.
- A DNS name for the host, with TCP 80 and 443 open to the internet: Caddy
  gets its certificate from Let's Encrypt. Caddy is the only container that
  publishes ports.
- 172.31.250.0/24 and 172.31.251.0/24 not used by another Docker network.
  The monitoring networks are fixed to them; compose stops with "Pool
  overlaps" otherwise.
- Access to Docker Hub, GitHub and PyPI while the image builds.

## Configure

The image copies `config/`, `adapters/` and `media/sources.yaml` at build
time, so changes take a rebuild.

- [config/relays.yaml](../config/relays.yaml): the relays jobs may publish
  to, their endpoints and drafts, and the `default`.
- [config/policy.yaml](../config/policy.yaml): set `namespace_prefix` to a
  name of your own. Publishers that share a relay need distinct prefixes, and
  `openmoq` is the OpenMOQ service's. The job limits are here too.
- [media/sources.yaml](../media/sources.yaml): the clips. OpenMOQ uses
  `akamai_test`, `nasa_tv` and `akamai_cmaf` with Akamai's permission.
  Without your own, keep to the Big Buck Bunny clips and the generated
  content (`clock`, `bars`, `noise`).

## Deploy on the host

```sh
git clone https://github.com/openmoq/moq-test-tracks
cd moq-test-tracks/deploy
cp .env.example .env && chmod 600 .env
# Set MTT_HOSTNAMES=<your DNS name> and MTT_PUBLIC_ORIGINS=https://<your DNS name> in .env.
for v in MTT_RUNNER_TOKEN MTT_TOKEN_SECRET MTT_SESSION_SECRET MTT_GRAFANA_ADMIN_PASSWORD; do
    sed -i "s/^$v=.*/$v=$(openssl rand -hex 32)/" .env
done
docker compose up -d --build
```

Keep a copy of `MTT_TOKEN_SECRET` off the host: every token is signed with
it ([admin guide](admin.md#backup)).

## Or deploy over SSH

`deploy/deploy.sh <ssh-host>` copies the committed tree (commit your config
first) to `~/moq-test-tracks` on the host, fills in missing secrets, builds
and starts the stack. Before the first run, create
`~/moq-test-tracks/deploy/.env` on the host with your `MTT_HOSTNAMES` and
`MTT_PUBLIC_ORIGINS`: without it the script copies `.env.example`, which
names moq-test.ci.openmoq.org. Later runs keep the host's `.env`.

## Check it

In `moq-test-tracks/deploy` on the host:

```sh
(umask 077; docker compose exec -T api mtt tokens create \
    --name you --role admin --relay <relay id> --expires-days 30 > you.token)
curl -s https://<host>/healthz
curl -s -X POST -H "Authorization: Bearer $(cat you.token)" \
    "https://<host>/api/v1/jobs/check?preset=720p30&relay=<relay id>"
```

`/healthz` answers `{"ok":true,"runner":true}`. The check answers
`"accept": true` with the namespace, and starts nothing. Log in to the
dashboard at `https://<host>/` with the token. The [admin guide](admin.md)
covers tokens for others.

## Clips

Generated content needs nothing. A clip plays once it is recorded into the
runner's volume:

```sh
docker compose exec -T runner mtt media fetch bbb_4k30
docker compose exec -T runner mtt media list
```

## Updating

Rerun `docker compose up -d --build` (or `deploy.sh`). It stops every
running job, and the runner does not resume them.
