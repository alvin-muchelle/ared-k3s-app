# README — k3s + Traefik + App (Frontend + Backend) — Debug, Permanent fixes, Deployment, GHCR, DNS

> This README was generated from the live troubleshooting session and contains the commands, observations, fixes and deployment steps taken on the **ared-distro** test device running **k3s**. It also includes next steps for DNS (CNAME / A record), PAT/registry guidance and Kubernetes manifest templates.

---

## Table of contents

1. Situation summary (what we observed)
2. Root causes discovered
3. Permanent fix applied for pod DNS/resolv.conf
4. Recreating CoreDNS and other system pods
5. Traefik NodePort, Ingress and TLS verification (what we did)
6. How to simulate `curl --resolve` behavior from a browser (Windows & Linux tips)
7. Commands to get node IP and NodePort
8. GHCR (ghcr.io) authentication, secrets and PAT guidance
9. Deploying frontend + backend images (manifests summary & imagePullSecrets)
10. Image tag best practices (latest vs digest-pinned)
11. Where to run `kubectl create secret docker-registry` (the device) and example
12. Next steps: attach a name (A/CNAME) for future DNS resolution
13. Troubleshooting checklist & useful commands

---

## 1) Situation summary

* Device `ared-distro` is running `k3s` (single-node cluster).
* Several system pods (CoreDNS, helm-install job, other pods) were in CrashLoopBackOff. CoreDNS logs showed `plugin/forward: no nameservers found` and k3s logs showed errors like `open etc/k3s-resolv.conf: no such file or directory`.
* `/etc/resolv.conf` on the host was using a local dnsmasq at `127.0.0.2` and not directly usable by pods.
* k3s was attempting to use a `etc/k3s-resolv.conf` (relative path in logs) which did not exist.
* Traefik was deployed as a Deployment in `kube-system`. A `NodePort` service was created for Traefik (mapped to node ports 30082, 30443, 32080). An `Ingress` (ared-ingress) with `host: traefik.local` and TLS secret `ared-tls` was applied in `default`.
* `curl -k --resolve traefik.local:30443:192.168.20.1 https://traefik.local:30443/` returned `200 OK` (JSON from the app) — which confirms Traefik is terminating TLS and routing to the app.

## 2) Root causes discovered

* k3s couldn't find the resolv file for POD sandboxes: log errors `open etc/k3s-resolv.conf: no such file or directory`.
* CoreDNS crashed with `plugin/forward: no nameservers found` because the upstream nameserver path the kubelet (k3s) was supposed to write/copy into pod sandboxes was missing or pointed to an empty file.
* The host's `/run/systemd/resolve/resolv.conf` can be empty on some distros (systemd-resolved stub behavior). That led to an empty source file when `kubelet` was told to use it.

## 3) Permanent fix applied (how to instruct kubelet which resolv.conf to copy)

**Goal:** Make kubelet copy a valid resolv.conf into pod sandboxes.

Steps performed / recommended:

1. Choose a stable resolv.conf path that kubelet can read. On this device we used `/etc/k3s-resolv.conf` (absolute path).
2. Populate that file with the host's working resolver configuration. Example:

```sh
sudo cp /etc/resolv.conf /etc/k3s-resolv.conf
# verify
sudo cat /etc/k3s-resolv.conf
```

3. Tell k3s (kubelet) to use that file by adding `kubelet-arg` in `/etc/rancher/k3s/config.yaml` (create it if not present):

```yaml
# /etc/rancher/k3s/config.yaml
kubelet-arg:
  - "resolv-conf=/etc/k3s-resolv.conf"
```

4. Restart k3s to pick up the config change:

```sh
sudo systemctl restart k3s
# or on some devices
sudo service k3s restart
```

5. Verify k3s started normally and no `open etc/k3s-resolv.conf` errors appear in `journalctl -u k3s -f`.

**Notes:**

* Using an absolute file path (e.g. `/etc/k3s-resolv.conf`) avoids ambiguity. Do not place a relative path in the kubelet arg.
* If your system uses `systemd-resolved` with an empty stub file, pick `/etc/resolv.conf` or create a static file you control and keep it updated.

## 4) Recreate CoreDNS pods (safe steps)

Once the resolv.conf issue is fixed, restart or recreate CoreDNS to pick up the correct resolver:

```sh
kubectl -n kube-system get pods -l k8s-app=kube-dns
# delete the pods so the controller recreates them
kubectl -n kube-system delete pod -l k8s-app=kube-dns
# OR restart the deployment (if CoreDNS deployed as deployment)
kubectl -n kube-system rollout restart deployment coredns
```

Watch logs for the coreDNS container:

```sh
kubectl -n kube-system logs -l k8s-app=kube-dns -c coredns -f
```

Expect `plugin/forward` warnings to disappear and `Ready` to become `True`.

## 5) Traefik NodePort, Ingress and TLS verification — what we did and why it works

* A Traefik Deployment is running in `kube-system` with entrypoints `8000` (web), `8443` (websecure) and `8080` (dashboard).
* A NodePort `traefik-nodeport` service exposes Traefik on the node: `80:30082`, `443:30443`, `8080:32080`.
* The ingress `ared-ingress` in `default` points `traefik.local` to service `ared-service` on port `80`. TLS secret `ared-tls` exists in `default`.
* From the node, this curl succeeded (simulating client with Host header):

```sh
curl -k --resolve traefik.local:30443:192.168.20.1 https://traefik.local:30443/
# returns the JSON payload from the app -> TLS + routing working
```

That command both forces DNS resolution to `192.168.20.1` for `traefik.local` and connects to node port `30443` which Traefik is listening on. Traefik matches the `Host: traefik.local:30443` and uses the TLS secret to terminate.

**Conclusion:** Traefik + TLS + ingress route to app is functioning when client resolves traefik.local to node IP and connects to NodePort 30443.

## 6) How to simulate `curl --resolve` from a browser

**Options (choose what suits you):**

### A — Edit your client hosts file (recommended if you have admin rights)

* Linux / macOS: edit `/etc/hosts` (requires `sudo`).
* Windows: edit `C:\Windows\System32\drivers\etc\hosts` (requires Administrator privileges). Add:

```
192.168.20.1 traefik.local
```

Then open `https://traefik.local:30443/` in the browser and accept the self-signed cert or import the `.crt` into the OS trust store (see below).

**Windows note:** you must run the editor as Administrator. If you can't run Notepad as Admin, run an elevated PowerShell and append the line:

```powershell
Start-Process notepad -Verb runAs
# OR -- elevated append (if you have admin rights already in that shell):
Add-Content -Path 'C:\Windows\System32\drivers\etc\hosts' -Value '192.168.20.1 traefik.local'
```

If you *absolutely cannot* edit hosts (no admin), use one of the methods below.

### B — Launch Chrome/Edge with host resolver rules (no hosts edit required)

Create a browser shortcut and append this flag to the target:

```
--host-resolver-rules="MAP traefik.local 192.168.20.1"
```

Example `Target` on Windows (in a shortcut):

```
"C:\Program Files\Google\Chrome\Application\chrome.exe" --host-resolver-rules="MAP traefik.local 192.168.20.1"
```

This makes the browser resolve `traefik.local` to the IP without changing system hosts.

### C — Use a browser extension to rewrite requests / set host header

There are extensions (like Requestly) that can map hostnames to IPs or rewrite headers. Use them to set `Host: traefik.local` while sending the request to `192.168.20.1:30443`.

### D — Import the self-signed certificate into OS/browser trust store to avoid warnings

Export the cert (from Kubernetes secret or `tls.crt` used earlier) and import to:

* Windows: `certmgr.msc` -> Trusted Root Certification Authorities (requires Admin)
* macOS: Keychain Access -> System -> Add and trust (requires Admin)

After importing, `https://traefik.local:30443/` will show as secure (if hostname matches cert CN / SAN).

## 7) Commands to get node IP and NodePort

* List nodes (show internal/External IP):

```sh
kubectl get nodes -o wide
# to print internal IP only (first node):
kubectl get nodes -o jsonpath='{range .items[0]}{.status.addresses[?(@.type=="InternalIP")].address}{end}\n'
```

* Show a service and its NodePort(s):

```sh
kubectl -n default get svc ared-service -o wide
# or get specific port value
kubectl -n default get svc ared-service -o jsonpath='{.spec.ports[*].nodePort}\n'
# convenience to see both node IP + nodePort to curl from outside:
NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}')
NODE_PORT=$(kubectl -n kube-system get svc traefik-nodeport -o jsonpath='{.spec.ports[?(@.name=="https")].nodePort}')
echo $NODE_IP:$NODE_PORT
```

## 8) GHCR (ghcr.io) authentication, secrets and PAT guidance

**Short answer:** You can use a Personal Access Token (classic) generated from a personal account to authenticate to `ghcr.io`, but that account must have the proper permissions to the package/repository in the organization. To download (pull) private packages you need `read:packages` on the PAT and your user must have read access. To publish (push) images, use `write:packages` and your account must have write rights.

**Important official points:**

* GitHub Packages requires a personal access token (classic) for authentication (not the newer fine-grained PAT). The PAT must include the correct scopes (`read:packages`, `write:packages`, `delete:packages` as needed). Your GitHub user account also needs matching repository/org permissions.
* For GitHub Actions running inside the same repository, the provided `GITHUB_TOKEN` can be used to publish/pull packages without storing a PAT.

(These two points are pulled from the GitHub Docs — see the citation in the chat.)

**Practical guidance:**

* If you only need to **pull** images from `ghcr.io/ared-group/...` in your k3s cluster: create a secret with `--docker-username=<YOUR_GH_USER>` and `--docker-password=<YOUR_PAT>` where `<YOUR_GH_USER>` is the GitHub username of the account that has access to those packages. That account can be your personal user **if** it is a member (or otherwise has read access) to the repos/packages in the org. The token must have `read:packages` scope.
* If you need to **push** to the org package space from CI or local runs, the PAT you use must belong to an account with write/publish permissions — typically either your user (if granted rights) or a machine/service account that has the proper permissions.
* Alternatives: use a **machine user** (a dedicated GitHub user account) or GitHub Apps for more controlled automation, or rely on Actions' `GITHUB_TOKEN` when running inside GitHub Actions.

**Example: create docker-registry secret on the device**

```sh
kubectl create secret docker-registry ghcr-secret \
  --docker-server=ghcr.io \
  --docker-username="YOUR_GH_USER" \
  --docker-password="YOUR_PAT" \
  --docker-email="you@example.com" \
  -n default
```

Place `imagePullSecrets:

* name: ghcr-secret` into your deployment YAML so k8s can pull private images.

## 9) Deploying frontend + backend images (manifests and imagePullSecrets)

**Short design**

* You should create **two Deployments** (backend & frontend), two Services and one Ingress (frontend), because typically only the frontend is exposed externally via Ingress. The frontend talks to the backend via internal service DNS (`http://backend-svc:8080`) or similar.

**Key points:**

* Use `imagePullSecrets` in both deployments if images are private.
* The frontend Deployment should include an Ingress/IngressRoute that maps the hostname (traefik.local) to the frontend service. The TLS secret `ared-tls` can be reused for the frontend Ingress.
* The backend need not have external Ingress unless you want direct external access.

(You previously asked for 6 manifest files. In the README doc attached I included templates for: `frontend-deployment.yaml`, `frontend-service.yaml`, `frontend-ingress.yaml`, `backend-deployment.yaml`, `backend-service.yaml`, and `backend-ingress.yaml` — these are stored in this README document itself.)

## 10) Image tag best practices

* `latest` is convenient for development but not recommended for production because it is not immutable and can make rollbacks / reproducibility difficult.
* Prefer digest-pinned images: `ghcr.io/org/repo/image@sha256:<digest>` — this guarantees the exact image.
* If using tags from CI, use a tag that contains `github.sha` or a release tag, or better store the digest and deploy using digest.

How to get digest locally after pushing:

```sh
docker pull ghcr.io/ared-group/survey-management-system/frontend:latest
docker inspect --format='{{index .RepoDigests 0}}' ghcr.io/ared-group/survey-management-system/frontend:latest
# result is like ghcr.io/ared-group/survey-management-system/frontend@sha256:abcdef...
```

Then put the digest string into your deployment yaml.

## 11) Where to run `kubectl create secret docker-registry` (the device)

* Run it **on the device** where `kubectl` is configured to talk to your k3s cluster (this can be the node itself — you are already `ssh`ed in there). The secret will be stored in the cluster in the selected namespace (e.g. `default`).
* Example (run on the node where kubeconfig points to the k3s cluster):

```sh
kubectl create secret docker-registry ghcr-secret \
  --docker-server=ghcr.io \
  --docker-username="YOUR_GH_USER" \
  --docker-password="YOUR_PAT" \
  --docker-email="you@example.com" \
  -n default
```

* After that, include `imagePullSecrets:

  * name: ghcr-secret`in your`Deployment` spec for both frontend and backend.

## 12) Next steps: attach a name/CNAME to the node IP for future DNS

Options for production-stable name resolution:

1. **Public DNS A record** (recommended for reachable nodes):

   * In your DNS provider dashboard, create an `A` record for `traefik.example.com` pointing to the node's public IP.
   * If you will scale to multiple nodes or use a LoadBalancer, point the domain to the LB IP.

2. **CNAME** — Useful if you already have a host name managed elsewhere. Create a CNAME pointing at a DNS host that resolves to node IP.

3. **Dynamic DNS** — If node IP changes, use a DDNS provider and point A or CNAME to the DDNS name.

4. **For local testing** — keep using `/etc/hosts` edits or Chrome `--host-resolver-rules` for quick testing.

**TLS propagation:** Once DNS resolves the name to the node IP, ensure your certificate SAN includes the hostname. For public certificates, use Let's Encrypt via a controller (IngressRoute + cert-manager or Traefik's ACME) or provide/manually create a certificate for that hostname and apply as Kubernetes secret.

## 13) Troubleshooting checklist & useful commands

* Check k3s logs:

```sh
sudo journalctl -u k3s -f
```

* Check CoreDNS logs:

```sh
kubectl -n kube-system get pods -l k8s-app=kube-dns -o wide
kubectl -n kube-system logs -l k8s-app=kube-dns -c coredns -f
```

* Check Traefik logs & routers/services via NodePort dashboard (bounded to 32080) or API if enabled.

