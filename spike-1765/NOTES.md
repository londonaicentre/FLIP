# Spike #1765: Flower net per project

Run on 3 November 2026 on a kind cluster on a Mac (`kind-flip-1765`, one CPU node, a fake `nvidia.com/gpu`
extended resource), with upstream `flwr/superlink:1.42.0` and `flwr/supernode:1.42.0`, and FLIP's fleet-proxy
image built from `fl-services/flower/fleet-proxy`. Namespace `hub` stands in for the hub and `trust` for one
Kubernetes trust. `./scenario.sh` reruns everything; `out/scenario.log` is the run quoted here.

**Verdict: go on the mechanism.** Every question came out yes, with no Flower patch and nothing from Flower's
Enterprise package. The open cost is the hub's half: creating SuperLinks at run time on ECS.

## Answers

1. **A new SuperLink, today's kit.**
   - The SuperNode holds only `ca.crt`, and a SuperLink issued later by the same CA works.
   - The trust-facing name is the fleet-proxy's. One wildcard certificate, `*.fleet.<domain>`, covers every project, so no certificate per project reaches a trust.
   - Each SuperLink's own certificate must name its Service, plus `localhost` and `127.0.0.1`. Without those two, the SuperLink's built-in SuperExec cannot reach its own Runtime API, and runs stay `pending` forever. FLIP's provisioned certificates already carry them.
2. **One listener for every project.**
   - nginx maps the host the SuperNode dialled, `project-<p>.fleet.<domain>`, to the Service `superlink-<p>` and resolves it per request (`k8s/fleet-proxy.conf.template`).
   - A new project needs only a new Service, never a proxy change or reload.
   - The name is limited to `[a-z0-9-]`, so it cannot leave the hub namespace, and the Fleet route allow-list is unchanged.
3. **Registration is per SuperLink.**
   - `flwr supernode register <pub> b` puts the key on SuperLink B only.
   - B's key dialling project A's host is refused: `activate-node` returns 412, "No SuperNode found with the given public key".
   - The SuperNode exits with code 305 and crash-loops. SuperLink A's node list is unchanged.
4. **Isolation.**
   - Two runs on two SuperLinks ran at the same time on one trust. Each reached only its own SuperNode and read only its own project's mount.
   - The key reached the ClientApp from a `secretKeyRef`, so it is not a literal in the pod spec.
   - With one GPU, Kubernetes ran A and held B Pending. B was scheduled as soon as a second GPU appeared.
5. **Lifecycle,** with the images already on the node:

   | Step | Time |
   |---|---|
   | SuperLink created → Control API answering | 9 s |
   | SuperNode created → online | 6–8 s |
   | Two-round run, start to finish | 20 s |
   | Teardown | 10 s |

   - Each round took about 6 s, mostly the SuperNode's pull interval.
   - Teardown means: unregister the node, delete the SuperNode Deployment, Secret and ConfigMap, then delete the SuperLink Deployment, Service and Secret. Nothing was left behind, and project A kept running afterwards.
   - FLIP's real images are about 7.5 GB. Spike #1673 measured a cold pull at 2 min 28 s, so a trust should keep the image cached.
6. **trust-api's Role in the trust namespace:**
   - `deployments` (apps): get, list, create, delete
   - `secrets` and `configmaps`: get, create, delete

   Checked with `kubectl auth can-i` as the ServiceAccount: it cannot create pods, exec, read logs, or touch any other namespace.

## Not tested here

- **The hub on ECS.** Each project's SuperLink would be its own ECS service or task, created by flip-api, with a Cloud Map name behind the fleet-proxy. Its ServerApp then runs in that project's own task: per-project compute on the hub too, which bears on W10.
- **Public DNS and the trust egress allow-list.** A wildcard record, `*.fleet.<domain>`, would point at the NLB. Trust allow-lists then need the wildcard. On LZA the SNI allow-list fails open (lza#56).
- **FLIP's own images, real GPUs, the dataframe and imaging APIs.** The ClientApp only proved what it can see.
- **Compose trusts.** They stay one project per net, since creating a SuperNode needs Kubernetes.

## Size, if built

| Piece | Size | Scope |
|---|---|---|
| Hub | L | SuperLink per project as an ECS service (task definition, Cloud Map, security groups), fleet-proxy host routing, wildcard certificate and DNS, `fl_scheduler` rows created and removed with the project, key registration |
| Trust | M | A task type in trust-api (create or delete the project SuperNode with its own key pair), a chart template and the Role above, reporting a SuperNode that is Pending or crash-looping back to the hub |
| Glue | S | The hub registers the trust's public key; the trust never receives a kit |
