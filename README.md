# README — k3s + Traefik + app (HTTPS) — full notes & next steps

This README collects everything from our troubleshooting session, the exact commands and configuration snippets you ran, what we discovered, how TLS ended up working, how to test from curl and a browser (Windows), and recommended next steps to attach a DNS name / CNAME to the node IP for future resolution.

---

## High-level goal

Run a small app in k3s on a single node, expose it through Traefik Ingress, terminate TLS on Traefik, and be able to access it from a browser over `https://traefik.local:30443/` (or similar) and confirm the app returns host/time JSON.

---

## Environment (important facts)

* Single k3s node `ared-distro` (control-plane & worker)

  * Node IP: `192.168.20.1`
  * k3s version: `v1.28.7-k3s1`
  * Container runtime: containerd
* Traefik installed via Helm (deployment `traefik` in `kube-system`)

  * Traefik Pod: `traefik-75845b8cf8-wfhw5`
  * Exposed via NodePort service `traefik-nodeport` (namespace `kube-system`)

    * NodePorts mapped: `80:30082`, `443:30443`, `8080:32080`
    * Endpoint IP for Traefik pod: `10.42.0.23`
* App deployed in `default` namespace with service `ared-service`:

  * ClusterIP: `10.43.9.63`
  * NodePort: `30081` → `targetPort:8080`
  * Endpoint: `10.42.0.22:8080` (pod `ared-k3s-app-6694748458-kgp6s`)
* Ingress `ared-ingress` in `default`:

  * `ingressClassName: traefik`
  * host: `traefik.local`
  * tls secret: `ared-tls` (exists in `default`)
* Host OS `resolv.conf` uses `127.0.0.2` (dnsmasq), and earlier k3s had issues with `etc/k3s-resolv.conf` missing.

---

## Problems found during troubleshooting

1. **CoreDNS CrashLoop** — logs `plugin/forward: no nameservers found` and CrashLoopBackOff. Cause: kubelet/pod sandbox DNS config problem linked to `etc/k3s-resolv.conf` missing or resolv.conf misconfiguration.

   * k3s logs: `open etc/k3s-resolv.conf: no such file or directory` and `Could not open resolv conf file`.
   * You tried to make kubelet use `resolv-conf=/run/systemd/resolve/resolv.conf` by editing `/etc/rancher/k3s/config.yaml`, but that file was empty on your system.

2. **Ingress & Traefik routing** — at first `curl` to node IP:30443 returned `404` (Traefik default). After creating NodePort service and Ingress + TLS secret, using `curl --resolve traefik.local:30443:192.168.20.1` returned a 200 OK and JSON from the app — meaning Traefik routing & TLS termination were functional.

3. **Certificate** — the TLS secret `ared-tls` contained a self-signed certificate. Browsers will not trust that by default.

4. **Testing from browsers** — browsers use OS DNS; while `curl --resolve` can bypass DNS, browsers require either:

   * adding `traefik.local` → `192.168.20.1` to OS `hosts` file, **and**
   * trusting the certificate (import into system trust store) OR using a CA-signed cert.

---

## Exact (useful) commands & config snippets used / reproduced

### Useful kubectl and debugging commands

```bash
kubectl -n kube-system get pods -o wide
kubectl get nodes -o wide
kubectl -n kube-system describe pod coredns-6799fbcd5-hw76q
kubectl -n kube-system logs coredns-6799fbcd5-hw76q -c coredns
kubectl -n kube-system get svc traefik-nodeport -o wide
kubectl -n default get svc ared-service -o wide
kubectl -n default get endpoints ared-service -o yaml
kubectl -n default get ingress ared-ingress -o yaml
```

### Traefik NodePort service applied (you created something like)

```yaml
# saved/applied with kubectl apply -f -
apiVersion: v1
kind: Service
metadata:
  name: traefik-nodeport
  namespace: kube-system
spec:
  type: NodePort
  selector:
    app.kubernetes.io/instance: traefik-kube-system
    app.kubernetes.io/name: traefik
  ports:
    - name: http
      port: 80
      targetPort: 8000
      nodePort: 30082
    - name: https
      port: 443
      targetPort: 8443
      nodePort: 30443
    - name: dashboard
      port: 8080
      targetPort: 8080
      nodePort: 32080
```

### Ingress (you applied)

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: ared-ingress
  namespace: default
spec:
  ingressClassName: traefik
  rules:
  - host: traefik.local
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: ared-service
            port: { number: 80 }
  tls:
  - hosts:
    - traefik.local
    secretName: ared-tls
```

### TLS secret existed (base64 data present) — secret `ared-tls` in namespace `default`.

> (I’m not reproducing whole base64 blobs here; you already have the secret.)

### Useful curl commands you used

* With node IP directly (Traefik default cert):

```bash
curl -k -v https://192.168.20.1:30443/
```

* With host override (SNI/Host header) — the one that worked:

```bash
curl -k --resolve traefik.local:30443:192.168.20.1 https://traefik.local:30443/ -v
# or (verify using the exported cert)
curl --cacert traefik.local.crt --resolve traefik.local:30443:192.168.20.1 https://traefik.local:30443/ -v
```

* To fetch TLS cert from the secret into a file:

```bash
kubectl -n default get secret ared-tls -o jsonpath='{.data.tls.crt}' | base64 --decode > traefik.local.crt
```

### k3s kubelet resolv-conf setting you added in `/etc/rancher/k3s/config.yaml`

```yaml
# instruct kubelet what resolv.conf to copy into pods
kubelet-arg:
  - "resolv-conf=/run/systemd/resolve/resolv.conf"
```

> Note: `/run/systemd/resolve/resolv.conf` on this system was empty. k3s reported errors like `open etc/k3s-resolv.conf: no such file or directory`. A common fix is to point kubelet at a working resolv.conf (e.g. `/etc/resolv.conf`) or create the expected `/etc/k3s-resolv.conf` and restart k3s.

---

## How TLS ended up working (summary of the happy path)

1. Traefik runs in k3s and exposes `websecure` on container port `8443`.
2. You created `traefik-nodeport` mapping `8443` → node port `30443`.
3. Ingress `ared-ingress` references host `traefik.local` and TLS secret `ared-tls`.
4. Traefik picks up the Ingress and the TLS secret, terminates TLS for host `traefik.local`, and forwards the request to `ared-service` (which routes to your pod).
5. `curl --resolve traefik.local:30443:192.168.20.1 https://traefik.local:30443/`:

   * forces `traefik.local` to resolve to the node IP,
   * makes a TLS handshake with Traefik (which presented the `traefik.local` cert),
   * Traefik routed the HTTPS request to your app and returned JSON — **200 OK**.

Therefore: **TLS termination and routing are correct and working** for the host `traefik.local` as long as the client SNI/Host is `traefik.local` and the client connects to the node IP:30443.

---

## How to reproduce what you did (concise step-by-step)

1. Ensure Traefik deployment is running in `kube-system` and NodePort service exists exposing 8443 → 30443:

   ```bash
   kubectl -n kube-system get deployment traefik
   kubectl -n kube-system get svc traefik-nodeport -o wide
   ```

2. Confirm your app and service:

   ```bash
   kubectl -n default get pods -l app=ared-k3s-app -o wide
   kubectl -n default get svc ared-service -o wide
   kubectl -n default get endpoints ared-service -o yaml
   ```

3. Confirm Ingress references `traefik.local` and `ared-tls`:

   ```bash
   kubectl -n default get ingress ared-ingress -o yaml
   kubectl -n default get secret ared-tls -o yaml
   ```

4. Test from the node using curl (works without modifying OS hosts because we used `--resolve`):

   ```bash
   curl -k --resolve traefik.local:30443:192.168.20.1 https://traefik.local:30443/
   # or to validate the cert without -k:
   kubectl -n default get secret ared-tls -o jsonpath='{.data.tls.crt}' | base64 --decode > traefik.local.crt
   curl --cacert traefik.local.crt --resolve traefik.local:30443:192.168.20.1 https://traefik.local:30443/
   ```

---

## How to access the HTTPS site from a browser (Windows example)

Browsers respect the OS DNS and certificate trust stores. To make `https://traefik.local:30443/` load without `--resolve` and without certificate errors, do both steps below.

### 1) Make `traefik.local` resolve to `192.168.20.1` (on Windows)

Run PowerShell **as Administrator** and append the hosts file:

```powershell
# Open an elevated PowerShell (Run as Administrator)
# Then append:
Add-Content -Path "$env:Windir\System32\drivers\etc\hosts" -Value "192.168.20.1 traefik.local"
# or if you prefer a direct echo:
"192.168.20.1 traefik.local" | Out-File -FilePath "$env:Windir\System32\drivers\etc\hosts" -Encoding ASCII -Append
```

If you can use Notepad elevated:

```powershell
Start-Process notepad.exe -Verb runAs
# then edit: C:\Windows\System32\drivers\etc\hosts  (add a line)
```

### 2) Trust the certificate in Windows

Export the certificate from Kubernetes (on the node or any machine with `kubectl`) and copy it to the Windows machine:

```bash
kubectl -n default get secret ared-tls -o jsonpath='{.data.tls.crt}' | base64 --decode > traefik.local.crt
# copy traefik.local.crt to the Windows machine
```

On the Windows machine (elevated PowerShell), import to the Trusted Root:

```powershell
certutil -addstore -f "Root" C:\path\to\traefik.local.crt
```

After import:

* Restart the browser.
* Navigate to `https://traefik.local:30443/`.
* It should load without certificate warnings and return the JSON from your app.

**If you do not import the cert** the browser will show a security warning (because it’s a self-signed cert). You can proceed past the warning in some browsers, but importing is cleaner.

---

## Alternate: Let’s Encrypt / ACME (recommended long-term)

If you want `traefik.local` accessible without manually trusting certs on clients, use a publicly trusted domain and configure Traefik’s ACME (Let’s Encrypt). Summary steps:

1. Get a real domain (e.g., `example.com`) or a subdomain you control (e.g., `app.example.com`).
2. Create an A record in your DNS that points `app.example.com` → `192.168.20.1`.

   * If behind NAT, configure your router to forward ports 80/443 to the node or use DNS + a public IP.
3. Configure Traefik to use ACME (via Helm values or static arguments) with a certificate resolver, for example:

   * enable `--certificatesResolvers.le.acme.httpChallenge.entryPoint=web`
   * or `--certificatesResolvers.le.acme.tlsChallenge=true`
   * supply an email for ACME
   * configure persistent storage for ACME JSON
4. Create an ingress for `app.example.com` and Traefik will obtain a certificate automatically.

(If you want, I can produce exact Helm values/Traefik configuration for ACME.)

---

## How to attach a NAME / CNAME to the node IP for future DNS resolution

You have a few options, depending on whether you control a public domain and whether the node IP is public or private:

### Option A — (Simple) Edit your DNS provider to add an A record (best for public IPs)

1. In the DNS admin for your domain add:

   * `ared.example.com` A record → `192.168.20.1`
2. TTL: your choice (lower for testing).
3. If you want `traefik.local` globally, you must own `local` TLD (you don’t), so pick a real domain.

### Option B — Use a CNAME to point at another name

1. If you own `example.com` and want `traefik.example.com`, you can create:

   * `traefik.example.com` CNAME → `some-other-host.example.com`
   * and make `some-other-host.example.com` A→ `192.168.20.1`.

### Option C — Use a dynamic DNS provider (if IP changes)

1. Sign up for a dynamic DNS service (DuckDNS, No-IP, etc).
2. Create a host `myhost.duckdns.org` → their service will map to your public IP and provide tools to update it when IP changes.
3. You can also create a CNAME from your domain to the dynamic DNS name.

### Option D — Use wildcard DNS services for local testing (less recommended)

* `nip.io` or `sslip.io` allow you to create hostnames that include the IP (like `traefik.192.168.20.1.nip.io`) which resolve to the IP automatically. This can be useful for demos but not for production.

### Important DNS/Network notes

* If your node IP `192.168.20.1` is private and you want external users to reach it, you must expose the node to the public internet (public IP or NAT + port-forward).
* For local LAN-only testing, editing each client’s `hosts` file is easiest.
* To obtain trusted TLS (Let’s Encrypt) for a name pointing to your node, the domain must be publicly resolvable to the node's public IPs (or use DNS challenge).

---

## Troubleshooting checklist (if things break again)

1. Verify Traefik pod is running:

   ```bash
   kubectl -n kube-system get pods -l app.kubernetes.io/name=traefik -o wide
   kubectl -n kube-system logs deployment/traefik --tail=200
   ```

2. Verify `traefik-nodeport` endpoints are present:

   ```bash
   kubectl -n kube-system get endpoints traefik-nodeport -o yaml
   ```

   There should be the Traefik pod IP and ports 8443 (https) etc.

3. Verify Ingress and TLS secret:

   ```bash
   kubectl -n default get ingress ared-ingress -o yaml
   kubectl -n default get secret ared-tls -o yaml
   ```

4. Confirm app service has endpoints (at least one Ready):

   ```bash
   kubectl -n default get endpoints ared-service -o yaml
   ```

5. Test from node:

   ```bash
   curl -k --resolve traefik.local:30443:192.168.20.1 https://traefik.local:30443/
   ```

6. If CoreDNS fails / pods cannot start because of DNS config:

   * Check `/etc/resolv.conf` on host.
   * Create a resolv conf copy for k3s if necessary:

     ```bash
     sudo cp /etc/resolv.conf /etc/k3s-resolv.conf
     # or set kubelet-arg to a valid resolv.conf in /etc/rancher/k3s/config.yaml
     sudo systemctl restart k3s
     ```
   * Recreate CoreDNS pods:

     ```bash
     kubectl -n kube-system delete pod -l k8s-app=kube-dns
     # or
     kubectl -n kube-system rollout restart deployment coredns
     ```
   * Check k3s logs: `journalctl -u k3s -f`

---

## Next concrete tasks I recommend (pick one)

1. **Short-term / dev-only** — keep self-signed TLS but trust it locally:

   * Add `traefik.local` to your clients’ hosts files.
   * Import `traefik.local.crt` into each client’s trusted root store.

2. **Long-term / proper** — use a real DNS name + ACME:

   * Get a real domain or subdomain.
   * Add A record to point to your node public IP.
   * Configure Traefik ACME (Let’s Encrypt) so certificates are automatically obtained and renewed.

3. **Fix k3s DNS pod instability (CoreDNS)**:

   * Make sure k3s/kubelet has a valid resolv-conf or create `/etc/k3s-resolv.conf` and restart k3s.
   * Recreate CoreDNS pods.
   * Confirm pods become Ready and cluster DNS works.

---

## Handy commands summary (copy/paste)

Export cert from secret:

```bash
kubectl -n default get secret ared-tls -o jsonpath='{.data.tls.crt}' | base64 --decode > traefik.local.crt
```

Test HTTPS (from node) with host override and ignore cert:

```bash
curl -k --resolve traefik.local:30443:192.168.20.1 https://traefik.local:30443/
```

Test HTTPS verifying against exported cert:

```bash
curl --cacert traefik.local.crt --resolve traefik.local:30443:192.168.20.1 https://traefik.local:30443/ -v
```

Add hosts entry on Windows (elevated PowerShell):

```powershell
# in elevated PowerShell:
Add-Content -Path "$env:Windir\System32\drivers\etc\hosts" -Value "192.168.20.1 traefik.local"
```

Import cert into Windows Trusted Root (elevated PowerShell):

```powershell
certutil -addstore -f "Root" C:\path\to\traefik.local.crt
```

Restart CoreDNS (if DNS broken):

```bash
kubectl -n kube-system rollout restart deployment coredns
# OR delete pods:
kubectl -n kube-system delete pod -l k8s-app=kube-dns
```

Create `traefik-nodeport` service (already applied earlier):

```bash
# apply the YAML shown earlier
kubectl -n kube-system apply -f traefik-nodeport.yaml
```

---

## Final status (from the session)

* **Working**: Traefik successfully terminates TLS and routes to your app when the client name is `traefik.local` and it connects to node IP `192.168.20.1` on nodeport `30443`.
* **Caveat**: The TLS cert is self-signed; browsers will not trust it until you import it or switch to Let’s Encrypt or another CA.
* **DNS**: `curl --resolve` was used to override DNS; to get browsers working you must either edit hosts file or set proper DNS (A/CNAME) records.
