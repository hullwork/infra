#!/usr/bin/env python3
"""Host hard gate for kubeadm migration: network segment conflict/disk margin/port occupation. If the exit code is non-0, up is rejected.

Cross-platform convention (2026-08-17): This module does not assume that the host is macOS. Routing table data source selected by platform
(Linux netstat often does not exist at all, net-tools has been deprecated), disk margin uses shutil instead of
Derive df (`-g` is a macOS-specific option), check that the target is the file system where LIMA_HOME is located instead of
Any hard-coded path."""
import ipaddress, os, pathlib, platform, shutil, socket, subprocess, sys


def _routes(netstat_text):
    for line in netstat_text.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 4 and parts[0] not in ("Destination",):
            yield parts[0], parts[3]


def _route_net(dest):
    #macOS short form of netstat -rn: "192.168.6" = 192.168.6.0/24, "10.64.0/11" = 10.64.0.0/11.
    #ipaddress.ip_network does not recognize the short format and first completes 4 segments before parsing; if there is no prefix, the number of segments is pushed to /8 /16 /24 /32.
    addr, _, prefix = dest.partition("/")
    octets = addr.split(".")
    if not (1 <= len(octets) <= 4) or not all(o.isdigit() and 0 <= int(o) <= 255 for o in octets):
        raise ValueError(dest)
    bits = prefix or str(8 * len(octets))
    full = ".".join(octets + ["0"] * (4 - len(octets)))
    return ipaddress.ip_network(f"{full}/{bits}", strict=False)


def _route_dests_iproute(text):
    """The first column of`ip -4 route`is already the full CIDR (or default/naked IP=/32)."""
    for line in text.splitlines():
        parts = line.split()
        if parts:
            yield parts[0]


def route_command():
    """Return (argv, source). Linux uses ip, and others (macOS) use netstat.

    It's not "replace parsing with the same command": netstat on Linux belongs to net-tools and is not pre-installed in most distributions.
    Hardtuning it gets a FileNotFoundError instead of a parsing error."""
    if platform.system() == "Linux":
        return (["ip", "-4", "route"], "ip")
    return (["netstat", "-rn"], "netstat")


def subnet_conflicts(cidr, route_text, source="netstat"):
    want = ipaddress.ip_network(cidr)
    dests = (_route_dests_iproute(route_text) if source == "ip"
             else (d for d, _iface in _routes(route_text)))
    for dest in dests:
        try:
            net = _route_net(dest)
        except ValueError:
            continue  #"default" / IPv6 / link#xx and other non-IPv4 targets
        if net.prefixlen <= 1:
            continue  #catch-all (0/0, 0/1, 128/1, fake-IP TUN takes over all) does not claim the specific network segment;
                      #The host of this architecture is directly connected to the VM network segment without L3 (spike verification: user-v2, only portForwards)
        if want.overlaps(net):
            return True
    return False


def disk_target():
    """The directory where the VM image is actually downloaded: LIMA_HOME, default ~/.lima.

    The directory does not exist before the first up, and it will fall back to the nearest existing ancestor - the same file system.
    The margin is the same. Check it instead of `/`: The host often points LIMA_HOME to the disk, and checking the root partition will
    A fully adequate machine may be misjudged as insufficient (or vice versa)."""
    p = pathlib.Path(os.environ.get("LIMA_HOME") or (pathlib.Path.home() / ".lima"))
    while not p.exists() and p != p.parent:
        p = p.parent
    return p


def disk_available_gib(path=None):
    #shutil.disk_usage instead of df: `df -g`'s -g is macOS specific (on Linux
    #invalid option), and `df -BG`/`--block-size` is not recognized by macOS. Pure Python
    #Both sides are correct, and a child process is saved.
    return shutil.disk_usage(pathlib.Path(path) if path else disk_target()).free / 1024 ** 3


def host_ports_free(ports):
    bad = []
    for p in ports:
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", p))
        except OSError:
            bad.append(p)
        finally:
            s.close()
    return bad


def selected_ports(clusters_spec, registry):
    """Pure logic for --clusters filtering: return the host ports declared by these clusters.

    An empty clusters_spec means every cluster.  An unknown cluster name raises
    KeyError instead of silently falling back to the full set, so a mistyped filter
    fails loudly.  The disk and subnet checks are never filtered: they are
    host-global properties, independent of which clusters are being brought up."""
    names = [c.strip() for c in clusters_spec.split(",") if c.strip()] \
        if clusters_spec else list(registry)
    unknown = [c for c in names if c not in registry]
    if unknown:
        raise KeyError(f"unknown clusters: {unknown}")
    return [p["host"] for n in names for p in registry[n]["port_forwards"]]


def main():
    import argparse
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))
    from kubeadm_profile import CLUSTERS  #Same directory: sys.path injection
    ap = argparse.ArgumentParser()
    ap.add_argument("--cidr", default="192.168.6.0/24")
    #KUBEADM_MIN_DISK_GIB lowers the disk threshold for CI and acceptance runs, the
    #same way preflight.sh exposes INFRA_MIN_DISK_KB.  The default keeps 40G in
    #reserve.  Lowering it does not weaken the port and subnet gates: a negative-path
    #acceptance run has to be able to reach the port branch instead of always failing
    #on the disk check first.
    ap.add_argument("--min-disk-gib", type=float,
                    default=float(os.environ.get("KUBEADM_MIN_DISK_GIB", "40")))
    ap.add_argument("--clusters", default="",
                    help="comma-separated cluster names; port check limited to "
                         "those clusters' host ports (empty = all clusters)")
    a = ap.parse_args()
    fail = []
    route_argv, route_source = route_command()
    if subnet_conflicts(a.cidr, subprocess.check_output(route_argv).decode(), route_source):
        fail.append(f"{a.cidr} conflicts with a host route; set KUBEADM_CIDR to another range")
    target = disk_target()
    if disk_available_gib(target) < a.min_disk_gib:
        fail.append(f"{target} disk space is low: less than {a.min_disk_gib}G free "
                    f"(peak budget ~50GiB); free space or point LIMA_HOME at a larger disk")
    try:
        ports = selected_ports(a.clusters, CLUSTERS)
    except KeyError as e:
        ports = []
        fail.append(str(e).strip("'"))
    busy = host_ports_free(ports)
    if busy:
        fail.append(f"host ports already in use: {busy}")
    for f in fail:
        print(f"preflight: {f}", file=sys.stderr)
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
