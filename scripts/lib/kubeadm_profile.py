#!/usr/bin/env python3
"""Pure logical registry for kubeadm migration: cluster/VM/static IP/port/rendering.

The bash glue (scripts/lib/kubeadm-bootstrap.sh) only consumes the data here via the CLI.
Ensure that IP planning, port conventions, and CIDR uniqueness have a single source of truth and can be pinned by pytest."""
import argparse
import sys

CIDR_PREFIX = "192.168.6"

def _vm(name, ip, cpus, mem, disk):
    return {"name": name, "ip": f"{CIDR_PREFIX}.{ip}", "cpus": cpus,
            "memory_gib": mem, "disk_gib": disk}

# The optional `vm` field specifies the hosting VM (default = CP/vms[0]).
# A worker carries the generic NodePort ingress so services using local traffic
# policy remain reachable even though the control plane is tainted.
#6443 → 1844x is API server forwarding (spike: the host has no 192.168.6.x direct route, access through portForwards).
CLUSTERS = {
    # Shared infrastructure cluster: Git/Argo CD only.
    "infra": {
        # The former infra PostgreSQL was retired after each workload cluster gained
        # its own database. 18446/15102 and the former 19032 DB port remain free.
        # 6C/6G leaves headroom for the GitOps hub.
        "vms": [_vm("infra", 22, 6, 6, 30)],
        "pod_cidr": "10.208.0.0/16", "service_cidr": "10.228.0.0/16",
        "push_port": 15101,
        "port_forwards": [{"guest": 6443, "host": 18445},
                          {"guest": 5000, "host": 15101},
                          {"guest": 30418, "host": 19418}]},
    "workload": {
        # A generic reference workload cluster. The worker may be managed by a
        # NodePool declaration and carries optional runtime/RWX capabilities.
        "vms": [_vm("workload", 24, 4, 6, 60), _vm("workload-w1", 26, 2, 2, 30)],
        "pod_cidr": "10.205.0.0/16", "service_cidr": "10.225.0.0/16",
        "push_port": 15103,
        "gvisor": True,
        "rwx_nfs": True,
        "port_forwards": [{"guest": 6443, "host": 18446},
                          {"guest": 30080, "host": 18080, "vm": "workload-w1"},
                          {"guest": 5000, "host": 15103}]},
}

#kube network DHCP reserved segment (2026-08-17 Task 7b §4.1 Structural repair of cross-connection accident).
#
#The user-v2 network daemon (limactl usernet) of lima 2.2.0 does not have any DHCP scope parameters:
#`limactl network create` only has --gateway/--interface/--mode, and the daemon itself only has
#--subnet/--leases/--mtu (dhcpEnd in networks.yaml only takes effect in shared/host mode).
#Allocation policy = sequential first-free from .1, keyed by MAC, no expiration, no release/mutation API.
#Static segment .11-.49 vs. e2e segment .100-.149 The entire segment is on its growth path: any new MAC
#(Rebuilt instances, ephemeral e2e VM) may get other people's static addresses → cross-virtual machine threading.
#
#Fix = use your own lease table to remove the reserved segment from the allocation pool:
#- ~/.lima/_networks/kube/leases.json is seeded by --leases when the daemon is started (actual test:
#The --leases used by the kube guard during operation are word-for-word consistent with this file; network create only registers the network,
#The guard is started lazily with the first VM connected to the network, and the pre-written files survive and are read as they are);
#- For the running daemon, use the naked DHCP DISCOVER of the synthesized MAC to occupy the space one by one (actual measurement DISCOVER
#That is, the accounting, OFFER unclaimed lease is still there; the guard does not pick the MAC family). kb_ensure_network
#Responsible for the former, kb_flood_dhcp_reservations (bootstrap library) provides the latter.
#Preserved MAC deterministic derivation: 02:6b:72:<prefix last byte>:<marker>:<host byte>, marker 00=
#Reserved (entered into leases.json)/01=Fill the hole (only entered into running guard memory). 02 = locally
#The administered bit has no structural collision with the 52:55:55:* family derived from lima by instance name.
DHCP_RESERVED_RANGES = ((11, 49), (100, 149))

#The interval in the pool that is not reserved and can be allocated freely (guarded as usual first-free):
#.1-.10 Initial dynamic area (starting point of guard sequence allocation)
#.50-.99 Dynamic area after static segment overflow
#.150-.254 Final state dynamic area (after the reserved section is full)
def dhcp_reservation_mac(host_octet, prefix=CIDR_PREFIX):
    prefix_last = int(prefix.rsplit(".", 1)[1])
    return "02:6b:72:%02x:00:%02x" % (prefix_last, host_octet)

def dhcp_reservations():
    return {f"{CIDR_PREFIX}.{n}": dhcp_reservation_mac(n)
            for lo, hi in DHCP_RESERVED_RANGES for n in range(lo, hi + 1)}

def dhcp_filler_mac(host_octet, prefix=CIDR_PREFIX):
    #For hole filling (reserved between segments .50-.99, etc.): derived from the same family but with marker byte 01,
    #Only the memory of the running guard will be entered, and leases.json will not be entered (the guard will return to the free pool after restarting, which is harmless).
    prefix_last = int(prefix.rsplit(".", 1)[1])
    return "02:6b:72:%02x:01:%02x" % (prefix_last, host_octet)

#Task 1 spike verified network declaration block (user-v2 named network kube, host has no direct route). Enter the original text, do not change it from memory.
LIMA_INTERFACES_BLOCK = """\
#SPIKE-VERIFIED (lima 2.2.0): Named user-v2 network `kube`. The host has no 192.168.6.x direct route,
#Host access is always through portForwards. The network is pre-built by the bootstrap library (non-idempotent, check if it exists first):
#   limactl network create kube --gateway 192.168.6.1/24
#(DHCP reserved segments are seeded by the kb_ensure_network write leases.json, see DHCP_RESERVED_RANGES.)
networks:
  - lima: kube
"""

CLOUD_INIT_TEMPLATE = """\
provision:
- mode: system
  script: |
    #!/bin/sh
    set -eux
    apt-get update -qq
    apt-get install -y -qq containerd apt-transport-https ca-certificates curl gpg
    mkdir -p /etc/apt/keyrings /etc/containerd
    containerd config default | tee /etc/containerd/config.toml >/dev/null
    sed -i 's/SystemdCgroup = false/SystemdCgroup = true/' /etc/containerd/config.toml
    systemctl restart containerd
    curl -fsSL https://pkgs.k8s.io/core:/stable:/v1.36/deb/Release.key \\
      | gpg --dearmor --yes -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg
    echo 'deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v1.36/deb/ /' \\
      > /etc/apt/sources.list.d/kubernetes.list
    apt-get update -qq
    apt-get install -y -qq kubelet=1.36.* kubeadm=1.36.* kubectl=1.36.*
    apt-mark hold kubelet kubeadm kubectl
    cat > /etc/netplan/60-static.yaml <<'NETPLAN'
    network:
      version: 2
      ethernets:
        eth0:
          dhcp4: true
          addresses: [{ip}/24]
    NETPLAN
    netplan apply
"""

#gVisor preinstalled section for clusters that declare the runtime capability.
#
#[critical] 2026-08-17 Include renderer: It previously required people to manually follow the README after each `render`
#Append 43 lines again, and render will indiscriminately rewrite all yaml in the directory - missing three times in the same day.
#The consequence of omission is not build failure but a silent runtime downgrade.
#The RuntimeClass is still there and the Pod is scheduled as usual; nothing breaks until the
#kubelet builds the container -- or worse,
#Fall to the default runc to run untrusted code without anyone noticing. Documentation warnings cannot prevent this failure; generated configuration does.
#
#Versions are pinned explicitly so rendered VMs remain reproducible.
#default_*_sha512 are checked into lima/*.yaml verbatim; drift between this renderer and
#those files is caught by tests/test_nodepool.py::test_vm_definitions_match_their_renderer_byte_for_byte.
#The architecture is branched by uname -m (runsc is a single-file binary distributed by architecture), containerd plugin
#The path is selected according to config version (noble's v2.3.3 writes version = 4, runsc must be mounted
#io.containerd.cri.v1.runtime family; old grpc.v1.cri paths are silently ignored in v3/v4).
GVISOR_RELEASE = "release/20260803.0"
GVISOR_SHA512 = {
    "x86_64": {"runsc": "8f87e9a0ed6bec3a50effed65694d2cc71bfe76c0b5e740dcaa58af5af42d83cd441b44d3505abdf3b79e15052dde842e9981fc22c068c05f9ef92e9215becc9",
                "shim": "e39cc100ef11b6e78a918de8717fb62ace73c5137407dfd92b083264dac9b4f070435fc1f271cd6453b8fdae34cdc01f7fabbe5736bf30af79e389be8ecb424a"},
    "aarch64": {"runsc": "4547ead374aceb85d8659492e7b4a176b900ba98f873e4eab7e877968ffc5a01021c4f809bbb047d2d9498779ffab56d05d79ad4fcfb0e10438cb6779504dc49",
                 "shim": "6fdbd646c808d6f8fcf97ebce45244995a117d7d00bab1a1ce3146b1b8dbbc8a11728f715792033f7e62f66af5f766746b230e9570f332a3660a2b2613344dfa"},
}

GVISOR_PROVISION_BLOCK = """\

    #gVisor (runsc) - generated by scripts/lib/kubeadm_profile.py, do not modify this section manually.
    #The version matches the pinned values above.
    gvisor_release="{release}"
    architecture=$(uname -m)
    case "$architecture" in
      x86_64)
        runsc_sha512="{x86_runsc}"
        shim_sha512="{x86_shim}"
        ;;
      aarch64)
        runsc_sha512="{arm_runsc}"
        shim_sha512="{arm_shim}"
        ;;
      *)
        echo "unsupported gVisor architecture: $architecture" >&2
        exit 1
        ;;
    esac
    install_dir=$(mktemp -d)
    release_url="https://storage.googleapis.com/gvisor/releases/${{gvisor_release}}/${{architecture}}"
    curl -fsSL "$release_url/runsc" -o "$install_dir/runsc"
    curl -fsSL "$release_url/containerd-shim-runsc-v1" -o "$install_dir/containerd-shim-runsc-v1"
    echo "$runsc_sha512  $install_dir/runsc" | sha512sum -c -
    echo "$shim_sha512  $install_dir/containerd-shim-runsc-v1" | sha512sum -c -
    install -o root -g root -m 0755 "$install_dir/runsc" "$install_dir/containerd-shim-runsc-v1" /usr/local/bin
    rm -rf "$install_dir"
    config_version="$(grep -E "^[[:space:]]*version[[:space:]]*=[[:space:]]*[0-9]+" /etc/containerd/config.toml | head -1 | grep -Eo "[0-9]+")"
    case "$config_version" in
      2) cri_plugin="io.containerd.grpc.v1.cri" ;;
      3|4) cri_plugin="io.containerd.cri.v1.runtime" ;;
      *) echo "unsupported containerd config version: ${{config_version:-missing}}" >&2; exit 1 ;;
    esac
    cat >> /etc/containerd/config.toml <<TOML
    [plugins."${{cri_plugin}}".containerd.runtimes.runsc]
      runtime_type = "io.containerd.runsc.v1"
    TOML
    mkdir -p /etc/containerd/conf.d
    cat > /etc/containerd/conf.d/gvisor.toml <<TOML
    [plugins."${{cri_plugin}}".containerd.runtimes.runsc]
      runtime_type = "io.containerd.runsc.v1"
    TOML
    systemctl restart containerd
"""


def gvisor_provision():
    return GVISOR_PROVISION_BLOCK.format(
        release=GVISOR_RELEASE,
        x86_runsc=GVISOR_SHA512["x86_64"]["runsc"], x86_shim=GVISOR_SHA512["x86_64"]["shim"],
        arm_runsc=GVISOR_SHA512["aarch64"]["runsc"], arm_shim=GVISOR_SHA512["aarch64"]["shim"])


#NFS RWX preinstalled segment: the server runs on the workload control-plane VM
#guest OS (nfs-kernel-server), not in-cluster Pod - control plane VM rebuilds it too
#Rebuild without occupying workload quota; w1 only installs nfs-common (required for kubelet to mount NFS).
#export is narrowed to the lima network segment; no_root_squash is required for container semantics (Pod writes volumes with any uid).
#[critical] cloud-init provision is only executed when **creating a VM for the first time**: the existing cluster is
#This renderer is the single source of truth for the local reference provider.
NFS_EXPORT_DIR = "/srv/workspaces"
NFS_LIMA_CIDR = "192.168.6.0/24"

NFS_SERVER_BLOCK = """\

    #NFS RWX server - generated by scripts/lib/kubeadm_profile.py, do not modify this section manually.
    #Installs the NFS server on the control-plane VM.
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq nfs-kernel-server
    mkdir -p {export_dir}
    chmod 0777 {export_dir}
    grep -q '^{export_dir} ' /etc/exports || printf '%s {cidr}(rw,async,no_subtree_check,no_root_squash)\\n' '{export_dir}' >> /etc/exports
    exportfs -ra
    systemctl enable --now nfs-server
"""

NFS_CLIENT_BLOCK = """\

    #NFS RWX client - generated by scripts/lib/kubeadm_profile.py, do not modify this section manually.
    #Kubelet requires nfs-common to mount NFS PV.
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq nfs-common
"""


def nfs_provision(role):
    block = NFS_SERVER_BLOCK if role == "server" else NFS_CLIENT_BLOCK
    return block.format(export_dir=NFS_EXPORT_DIR, cidr=NFS_LIMA_CIDR)


def ip_of(vm_name):
    for c in CLUSTERS.values():
        for vm in c["vms"]:
            if vm["name"] == vm_name:
                return vm["ip"]
    raise KeyError(vm_name)

def context_of(cluster):
    if cluster not in CLUSTERS:
        raise KeyError(cluster)
    return cluster

def kubeconfig_of(cluster):
    #File name only, no directory. The on-host state directory belongs to one resolver in the
    #shell (kb_state_dir); naming it here as well is how this file and common.sh came to point
    #at two different directories, leaving `make bootstrap` chmod-ing a path nothing had written.
    if cluster not in CLUSTERS:
        raise KeyError(cluster)
    return f"kubeconfig-{cluster}"

def forwards_for(spec, vm_name):
    """Distribute cluster-wide port_forwards to specified VMs by optional vm field (defaults to CP = vms[0]).

    spec only needs to contain two keys: vms / port_forwards, and the CLUSTERS entry is isomorphic to the return value of ephemeral_spec.
    Entries pointing to non-existent VMs directly raise KeyError - the forwarding table is the hardest hit area for transcription errors, and it is better to make a sound than to lose it silently."""
    default = spec["vms"][0]["name"]
    names = {v["name"] for v in spec["vms"]}
    picked = []
    for p in spec["port_forwards"]:
        target = p.get("vm", default)
        if target not in names:
            raise KeyError(f"port_forwards references a VM that does not exist: {target}")
        if target == vm_name:
            picked.append(p)
    return picked

def render_lima_vm(vm, port_forwards, gvisor=False, rwx_role=None):
    #static: true - lima performs guest monitoring detection on forwarding without static, NodePort (kube-proxy/
    #Cilium) no kernel monitoring → the host side will never bind (the real root cause of Task 6); static means the host is unconditional
    #bind, port conflicts are exposed when the VM is started, consistent with preflight semantics. All entries are added together.
    pfs = "\n".join(
        f"  - guestPort: {p['guest']}\n    hostPort: {p['host']}\n"
        f"    static: true" for p in port_forwards)
    pf_block = f"portForwards:\n{pfs}\n" if port_forwards else ""
    #[critical] Do not write vmType, do not write top-level arch, and keep both architecture
    #variants under images -- the rendered artifact is then identical no matter which
    #It doesn't matter which machine it is rendered on, the same yaml works on Apple Silicon Mac and x86_64 Linux
    #You can directly run limactl start. These three items are all built-in defaults of lima:
    #vmType default = vz(macOS>=13.5) / qemu(rest, including Linux);
    #arch default = 'default' = host architecture;
    #images multiple variants = take the first one after filtering by target architecture.
    #Pinning any one of them breaks the other platform outright (vz does not exist on Linux; an aarch64 image on
    #On x86_64, we can only rely on TCG full system simulation, which is so slow that kubeadm init cannot finish running).
    return f"""\
images:
- location: "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img"
  arch: "x86_64"
- location: "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-arm64.img"
  arch: "aarch64"
cpus: {vm['cpus']}
memory: "{vm['memory_gib']}GiB"
disk: "{vm['disk_gib']}GiB"
{LIMA_INTERFACES_BLOCK}{pf_block}{CLOUD_INIT_TEMPLATE.format(ip=vm['ip'])}{gvisor_provision() if gvisor else ""}{nfs_provision(rwx_role) if rwx_role else ""}"""

def ephemeral_spec(run_id):
    import hashlib
    slot = 100 + int(hashlib.md5(run_id.encode()).hexdigest()[:4], 16) % 50
    return {
        "vms": [
            {"name": f"e2e-{run_id}-cp", "ip": f"{CIDR_PREFIX}.{slot}",
             "cpus": 2, "memory_gib": 2.5, "disk_gib": 20},
            {"name": f"e2e-{run_id}-w1", "ip": f"{CIDR_PREFIX}.{slot + 1}",
             "cpus": 2, "memory_gib": 3, "disk_gib": 20}],
        "pod_cidr": "10.250.0.0/16", "service_cidr": "10.251.0.0/16",
        #The api/http host ports are also distributed along with the slot (28000+idx / 28200+idx): static forwarding host
        #Unconditional bind: concurrent ephemeral clusters fail to start the VM if they share a
        #fixed port, so the three ranges (28000-28049 / 28200-28249 / 15900-15949) neither
        #overlap each other nor collide with the resident cluster.
        #Collisions in the same slot (different run_id hashes in the same slot) are blocked by preflight.
        "push_port": 15900 + (slot - 100),
        "port_forwards": [{"guest": 6443, "host": 28000 + (slot - 100)},
                          {"guest": 30080, "host": 28200 + (slot - 100),
                           "vm": f"e2e-{run_id}-w1"},
                          {"guest": 5000, "host": 15900 + (slot - 100)}]}

def merge_dhcp_reservations(path):
    """Merge reserved sections into leases.json (idempotent).  A reserved entry at a reserved
    address always wins -- that is what reserving means -- and every other existing entry is
    kept as is, so leases held by running VMs are not lost.
    Returns the number of new entries this time."""
    import json, os, pathlib
    p = pathlib.Path(path)
    existing = {}
    if p.exists() and p.stat().st_size:
        existing = json.loads(p.read_text(encoding="utf-8") or "{}")
    added = 0
    for ip, mac in dhcp_reservations().items():
        if existing.get(ip) != mac:
            added += 1
    existing.update(dhcp_reservations())
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return added

def dhcp_flood_plan(live_leases):
    """Given the lease table of the running guard (JSON of /leases), return the list of MACs that need to be placed.
    The allocation is [1..keep the upper bound] in order first-free: send a DISCOVER to each free address.
    Each one occupies a single slot.  Reserved addresses get their reserved MAC; the gaps
    between ranges (for example .50-.99) get a filler MAC, which never enters leases.json and
    returns to the free pool once the daemon restarts."""
    reserved = dhcp_reservations()
    macs = []
    top = max(hi for _, hi in DHCP_RESERVED_RANGES)
    for n in range(1, top + 1):
        ip = f"{CIDR_PREFIX}.{n}"
        if ip in live_leases:
            continue
        macs.append(reserved.get(ip) or dhcp_filler_mac(n))
    return macs

def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render"); r.add_argument("--out", required=True)
    s = sub.add_parser("show"); s.add_argument("cluster")
    i = sub.add_parser("ip-of"); i.add_argument("cluster"); i.add_argument("vm")
    e = sub.add_parser("ephemeral"); e.add_argument("runid")
    v = sub.add_parser("reserve-dhcp"); v.add_argument("--file", required=True)
    f = sub.add_parser("flood-plan"); f.add_argument("--live", required=True)
    args = ap.parse_args()
    if args.cmd == "render":
        import pathlib
        pathlib.Path(args.out).mkdir(parents=True, exist_ok=True)
        for cname, c in CLUSTERS.items():
            for vm in c["vms"]:
                #Cluster-level forwarding is distributed to the yaml of the corresponding VM according to the vm field (default belongs to CP):
                #The same host port can only be bound by one VM, and the workload port follows the workload.
                #The gvisor flag follows the cluster: only workload needs runsc, and the rest of the cluster renders
                #The provision is verbatim the same as before (no differences before and after the renderer compilation manual section).
                pathlib.Path(args.out).joinpath(f"{vm['name']}.yaml").write_text(
                    render_lima_vm(vm, forwards_for(c, vm["name"]), c.get("gvisor", False),
                                   rwx_role=("server" if c.get("rwx_nfs") and vm is c["vms"][0]
                                             else ("client" if c.get("rwx_nfs") else None))),
                    encoding="utf-8")
        print(f"rendered to {args.out}")
    elif args.cmd == "reserve-dhcp":
        print(merge_dhcp_reservations(args.file))
    elif args.cmd == "flood-plan":
        import json, pathlib
        live = json.loads(pathlib.Path(args.live).read_text(encoding="utf-8") or "{}")
        for mac in dhcp_flood_plan(live):
            print(mac)
    elif args.cmd == "show":
        c = CLUSTERS[args.cluster]
        print(f"ips={ [v['ip'] for v in c['vms']] } context={args.cluster} "
              f"kubeconfig={kubeconfig_of(args.cluster)}")
        print(f"vms={','.join(v['name'] for v in c['vms'])} pod={c['pod_cidr']} "
              f"svc={c['service_cidr']} push_port={c['push_port']}")
    elif args.cmd == "ip-of":
        #Single VM static IP (bridge/NP) is used by consumers who "follow the specific node"; please note that the
        #The netplan static segment is not the node InternalIP reported by kubelet - the latter is currently
        #The DHCP secondary address of eth0 will float in the unreserved segment).
        for vm in CLUSTERS[args.cluster]["vms"]:
            if vm["name"] == args.vm:
                print(vm["ip"])
                return 0
        print(f"unknown vm: {args.vm} (cluster {args.cluster})", file=sys.stderr)
        return 1
    else:
        print(ephemeral_spec(args.runid))

if __name__ == "__main__":
    sys.exit(main())
