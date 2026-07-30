# Cross-trust payload encryption — keys and rotation

Payloads exchanged between the hub and trusts (task payloads carrying cohort SQL,
`encrypted_project_id`, XNAT credentials) are encrypted with **AES-256-GCM** by
`utils/encryption.py` in `flip-api`, `trust-api`, `imaging-api` and
`data-access-api` (FLIP-PT-004).

GCM is authenticated: a tampered ciphertext raises `InvalidTag` instead of
decrypting to attacker-influenced bytes, which the previous AES-CBC scheme
allowed. The envelope's key-id is bound into the authentication tag, so it cannot
be relabelled either.

## Envelope

Base64 of `{"v": 1, "kid": ..., "iv": ..., "ct": ...}` — `iv` is a 12-byte GCM
nonce, `ct` is ciphertext‖tag. The algorithm is fixed rather than negotiated
(no algorithm-confusion surface); `v` exists so the envelope can be changed later.

## Keys

| Variable | Where | Purpose |
|---|---|---|
| `AES_KEY_BASE64` | hub + all trusts | Shared key, registered under kid `shared`. Used when nothing else is set. |
| `TRUST_AES_KID` + `TRUST_AES_KEY_BASE64` | one trust | That trust's own key. When set, it is used for everything that service encrypts. |
| `AES_TRUST_KEYS` | hub | JSON `{kid: base64_key}` of per-trust keys, so the hub can encrypt to a specific trust. |

`register_trust` mints a per-trust key and kid (`trust-<trust_id>`) at
registration, returned in the kit as `trust_aes_key` / `trust_aes_kid`.

## Giving a trust its own key

Per-trust keys contain the blast radius: with one shared key, a single
compromised trust yields the key for the whole federation.

1. Put the trust's key in the hub's `AES_TRUST_KEYS` under `trust-<trust_id>`
   (source it from a secret store — **not** the application database, or a DB
   compromise becomes a federation-wide key compromise).
2. Set `TRUST_AES_KID` / `TRUST_AES_KEY_BASE64` in that trust's kit
   (`trust/.env.<CODE>.<env>`), distributed out-of-band like
   `TRUST_INTERNAL_SERVICE_KEY`.

`kid_for_trust()` then selects that key automatically; trusts without one keep
using the shared key, so trusts can be moved over one at a time.

### What a per-trust key does and does not cover

Hub → trust payloads are per-trust keyed only where one ciphertext has exactly one
reader. That is the task-dispatch path (`GET /tasks/pending`, whose whole batch
belongs to the polling trust) and the cohort-query submission, which encrypts the
project id once per trust.

The FL training payload is **deliberately left on the shared key**:
`fl_service.start_training` hands a single ciphertext to the FL server, which fans it
out to every participating client. Encrypting under one trust's key would make it
undecryptable for the rest. Narrowing it needs the FL server to carry a per-client
payload — a protocol change, not a different `kid` at the call site.

Trust-internal encryption (imaging-api → data-access-api) needs nothing special: on a
trust, `_default_kid()` already resolves to that trust's own `TRUST_AES_KID`.

### Getting only half of it configured

The two halves are provisioned separately and nothing joins them up, so a partial
rollout fails asymmetrically:

* **hub → trust degrades silently.** `kid_for_trust()` does not find the trust's kid
  in the hub's keyring and falls back to the shared key. The trust decrypts it fine.
  Everything works, without the isolation you configured, and nothing says so.
* **trust → hub fails loudly**, with `KeyError: No key registered for kid
  'trust-<uuid>'`.

Because the quiet direction is the dangerous one, flip-api audits the keyring at
startup (`trusts_services/services/trust_key_config.py`) and logs per-trust key
coverage. Two operator errors are logged at error level: a kid in `AES_TRUST_KEYS`
matching no registered trust (a typo or a deleted trust — the key is loaded but never
selected, which is indistinguishable at runtime from "no key yet"), and a key that is
not a valid AES length. A malformed `AES_TRUST_KEYS` now fails at boot rather than on
the first request that encrypts.

`_keyring()` is memoised for the process lifetime, so adding a key needs a restart.

Both registration paths now deliver step 2 automatically: `register-trust
KIT=<CODE>` writes the two variables into the kit file (they are credential keys
in `scripts/trust_kit_lib.py`, so they are written once on a new registration and
never clobbered on the idempotent skip path), and the admin UI's kit modal
includes them in its copy-all block.

> Step 1 is still manual. The hub does not persist the AES key anywhere — a
> symmetric key cannot be reduced to a hash the way the api key can — so the value
> shown at registration is the only copy. Record it into `AES_TRUST_KEYS` at the
> same time, or the trust has to be re-registered to obtain another.

## Rolling this out to an existing deployment

`decrypt()` also accepts the previous **AES-CBC** format, so a hub and a trust on
different builds still understand each other while hosts are upgraded — which
matters for on-prem trusts that cannot all be restarted at once. `encrypt()` is
GCM-only, so no new unauthenticated ciphertext is produced, and nothing is stored
encrypted at rest (task payloads are held as plaintext in the hub database and
encrypted at dispatch), so there is no data to migrate.

There is also no key change: `AES_KEY_BASE64` stays as it is, and per-trust keys
are inert until provisioned. A trust operator changes no configuration — this is
a code deploy only.

1. Deploy the hub and each trust, in any order and at any pace.
2. When every host is upgraded, set `AES_ACCEPT_LEGACY_CBC=false` and confirm
   nothing breaks — that proves no peer is still sending CBC.
3. Delete the shim (`_decrypt_legacy_cbc`, `_accept_legacy_cbc`, the fallback
   branch in `decrypt`, and the CBC imports) from all four services.

One caveat during the upgrade: the hub marks a task `IN_PROGRESS` when it
dispatches it, so a task collected by a peer that cannot read it stays
`IN_PROGRESS`. Reset any such rows to `PENDING` afterwards.

## Rotation

Because every payload names its key, receivers can hold several keys at once:
add the new key to the keyring, switch the sender to it, then drop the old one.
No simultaneous change across the hub and every trust.

Note that some ciphertext is stored, not just in flight — queued `TrustTask`
payloads and `User.encrypted_password` — so re-encrypt or drain those before
removing a key they were encrypted under.
