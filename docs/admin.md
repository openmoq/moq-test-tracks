# Administering moq-test-tracks

This guide is for the service operators: issuing and revoking tokens,
choosing what a token allows, and keeping the service recoverable. Token
holders read the [user guide](user-guide.md); the endpoints are in the
[API reference](api.md).

## Tokens

Every call except `/healthz` and the namespace codec needs a token. A token
is self-contained: it carries who holds it and what it allows, and the
service checks its signature instead of looking it up. The only thing it
looks up is the short list of revoked token ids.

A token states:

| Claim | Meaning |
|-------|---------|
| holder | a name, `--name` (1–64 letters, digits, space, `.`, `_`, `@` or `-`) |
| role | `view`, `run` or `admin`, `--role` |
| tier | `standard` or `unrestricted`, `--tier` |
| relays | the relay ids it may publish to, `--relay` (repeat for more) |
| expiry | `--expires-days`: 30 by default, at most 365; at most 30 for `unrestricted` |
| id | 16 hex digits, generated; used to revoke it and to own its jobs |

Nothing in a token can be changed after it is issued. To change what someone
may do, issue a new token and revoke the old one.

### Roles

| Role | Can |
|------|-----|
| `view` | read jobs, tools, relays and clips; follow job events |
| `run` | `view`, plus check, start and stop its own jobs |
| `admin` | `run`, plus stop any job and stop all jobs; Grafana Admin |

### Tiers

Limits live in [config/policy.yaml](../config/policy.yaml).

| Tier | Limits |
|------|--------|
| `standard` | `per_key_defaults`: height up to 1080, up to 60 fps, video up to 8000 kbps, TTL up to 1 h (30 min default), 16 jobs per token |
| `unrestricted` | past those, up to the `global` caps: 16 jobs in total, 2 live encodes, TTL up to 24 h |

Admin tokens get the `unrestricted` limits whatever their tier. The global
caps bind everyone.

### Relay grants

A token publishes only to the relays it names. Each must be an id in
[config/relays.yaml](../config/relays.yaml) when the token is issued:

```sh
mtt tokens create --name alice --relay moqx-main --relay us-ord-3
```

There is no implicit list: a token without `--relay` can start nothing, and
a relay added to the catalog later is not granted to existing tokens.
`/api/v1/relays` shows each holder only their relays, and a job that names
another relay is refused with 403. When a job names no relay, the service
default (`moqx-main`) is used if granted, else the token's first relay.

A `view` token needs no relays. `run` and `admin` tokens need at least one.

## Issuing

Tokens are issued by the `mtt tokens` CLI inside the api container, which
holds the signing secret. Never over HTTP. On the host:

```sh
cd ~/moq-test-tracks/deploy
(umask 077; docker compose exec -T api mtt tokens create \
    --name peter-chave --role run --relay us-ord-3 --expires-days 90 > peter-chave.token)
```

or from a machine with SSH access to the host:

```sh
(umask 077; ssh moq-test 'cd moq-test-tracks/deploy && docker compose exec -T api \
    mtt tokens create --name peter-chave --role run --relay us-ord-3' > peter-chave.token)
```

The token goes to standard output once; a summary line (id, role, tier,
relays, expiry, holder) goes to standard error. `umask 077` keeps the file
readable only by you. Run directly (not in the container), `--out FILE`
writes a new mode-0600 file and never overwrites one.

### Use cases

| Who | Command |
|-----|---------|
| A tester on our relays, normal limits | `--name NAME --role run --relay moqx-main --relay aiomoqt-relay` |
| A partner confined to their own relay | `--name NAME --role run --relay us-ord-3 --expires-days 90` (their relay must be in `config/relays.yaml`) |
| A trusted tester, high limits, for a month | `--name NAME --role run --tier unrestricted --relay moqx-main --relay aiomoqt-relay --relay us-ord-3 --expires-days 30` |
| Someone who only watches | `--name NAME --role view` |
| An automated soak or CI job | `--name SYSTEM-soak --role run --relay moqx-main --relay aiomoqt-relay --expires-days 90`; one token per system, named for it |
| Another operator | `--name NAME --role admin --relay moqx-main --relay aiomoqt-relay --relay us-ord-3 --expires-days 90` |

Prefer the narrowest grant that does the job, and the shortest expiry that
will not interrupt the work.

### Handing a token over

A token is a bearer credential: whoever has it can use it. Send it through a
password manager share or another end-to-end encrypted channel, never in an
issue, a pull request, a shared chat channel or email. Send the holder the
[user guide](user-guide.md).

## Inspecting

```sh
docker compose exec -T api mtt tokens list
docker compose exec -T api mtt tokens show < peter-chave.token
```

`list` prints every issued token from the audit log: id, role, tier, status
(`active`, `expired`, `revoked`), expiry, relays and holder. `show` verifies
a token read from standard input (never pass one on the command line, where
`ps` shows it) and prints its claims and status. Holders see their own token
with `GET /api/v1/token`.

## Revoking

```sh
docker compose exec -T api mtt tokens revoke 2fb4139e28322b7d --reason "left the project"
```

Revocation is immediate: the next request with the token, and every
dashboard session made from it, gets 401. It does not stop the token's
running jobs; stop them with an admin token (`DELETE /api/v1/jobs/{id}`, or
`POST /api/v1/admin/stop-all`). Any id can be revoked, including one this
database has no record of, which is how revocations are redone after a
restore.

### Rotating the signing secret

Changing `MTT_TOKEN_SECRET` invalidates every token at once: use it when the
secret may have leaked, or to retire all tokens. On the host:

```sh
cd ~/moq-test-tracks/deploy
sed -i "s/^MTT_TOKEN_SECRET=.*/MTT_TOKEN_SECRET=$(openssl rand -hex 32)/" .env
docker compose up -d api
```

Then issue new tokens. `MTT_SESSION_SECRET` signs dashboard sessions only;
rotating it logs everyone out of the dashboard and leaves tokens valid.

## Auditing

The `audit` table in the api database records `token_created`,
`token_revoked`, `login`, `login_failed`, `job_started`, `job_joined`,
`job_stopped`, `job_killed` and `stop_all`, with the token id and client
address. Issued tokens are recorded by their claims, never the token itself.

## Grafana

`/grafana/` is behind the dashboard login: Caddy asks the api to accept the
session or token first. Admin tokens are Grafana Admins, all others Viewers.
Grafana's own login is off; its admin password in `deploy/.env` is for
break-glass use over SSH only.

## Backup

Tokens verify against `MTT_TOKEN_SECRET` alone, so losing the database
interrupts no one. It holds the revocation list and the audit history.

| What | Where | If lost |
|------|-------|---------|
| `MTT_TOKEN_SECRET` | `deploy/.env` on the host | every token must be reissued |
| api database | `/var/lib/mtt/api.sqlite` in the `mtt-api` volume | revoked tokens work again until revoked again; the audit history is gone |

Keep a copy of `MTT_TOKEN_SECRET` off the host, in the operators' password
manager. A leaked copy can mint any token, so it gets the same care as the
host's SSH keys.

To copy the database out while the service runs (a consistent snapshot,
streamed to the machine you run this on):

```sh
(umask 077; ssh moq-test 'cd moq-test-tracks/deploy && docker compose exec -T api python -c "
import sqlite3, sys, tempfile
with tempfile.NamedTemporaryFile(suffix=\".sqlite\") as f:
    dst = sqlite3.connect(f.name)
    sqlite3.connect(\"/var/lib/mtt/api.sqlite\").backup(dst)
    dst.close()
    sys.stdout.buffer.write(open(f.name, \"rb\").read())
"' > "api-$(date -u +%F).sqlite")
```

To restore:

1. Put `MTT_TOKEN_SECRET` back in `deploy/.env` if the host was rebuilt, and
   deploy.
2. Stream the copy back in and restart the api:

   ```sh
   ssh moq-test 'cd moq-test-tracks/deploy && docker compose exec -T api python -c "
   import sqlite3, sys, tempfile
   with tempfile.NamedTemporaryFile(suffix=\".sqlite\") as f:
       f.write(sys.stdin.buffer.read())
       f.flush()
       sqlite3.connect(f.name).backup(sqlite3.connect(\"/var/lib/mtt/api.sqlite\"))
   " && docker compose restart api' < api-YYYY-MM-DD.sqlite
   ```

3. Revoke again every token revoked since that copy was made
   (`mtt tokens revoke <id>`).

Tokens keep working throughout. A nightly copy off the host is not set up
yet.

## Token format

`mtt1_` followed by the base64url (no padding) of a CBOR Web Token
([RFC 8392](https://www.rfc-editor.org/rfc/rfc8392)): a COSE_Mac0
([RFC 9052](https://www.rfc-editor.org/rfc/rfc9052)) with HMAC 256/256, tagged
CWT. This is the structure of a Common Access Token (CTA-5007). The protected
header carries the algorithm and a key id derived from the secret. The claims
are `iss` (`mtt`), `sub` (holder), `exp`, `iat`, `cti` (8 bytes, the id) and
the private claim `mtt`: `{1: role, 2: [relay ids], 3: tier}`, with `3` left
out for `standard`. A token is 145–200 characters.

The service decodes nothing in a token until its MAC verifies: it reads the
outer structure with a strict parser and only then parses the claims.
