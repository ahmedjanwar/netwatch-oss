#!/usr/bin/env bash
# Creates the "netwatch-lab" Ubuntu VM in UTM: a sandbox with its own fake home network,
# isolated from the real one (UTM "shared" network = NAT behind this Mac).
#   ./create-vm.sh /path/to/ubuntu-24.04-server-cloudimg-arm64.img
set -euo pipefail
cd "$(dirname "$0")"
IMG=${1:?path to the Ubuntu 24.04 arm64 cloud image}
NAME=netwatch-lab
MAC=02:4e:57:1a:b0:01          # fixed, so we can find the VM's address in the Mac's DHCP leases
UTMCTL=/Applications/UTM.app/Contents/MacOS/utmctl
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

if "$UTMCTL" list | awk '{print $3}' | grep -qx "$NAME"; then
  echo "A UTM VM called $NAME already exists. Delete it in UTM first to rebuild." >&2; exit 1
fi
PUBKEY=$(cat ~/.ssh/id_ed25519.pub)

# --- cloud-init seed (NoCloud): user, SSH key, Docker, the lab network and fake devices ---
mkdir -p "$WORK/seed"
printf 'instance-id: %s-%s\nlocal-hostname: %s\n' "$NAME" "$(date +%s)" "$NAME" > "$WORK/seed/meta-data"
python3 - "$WORK/seed/user-data" "$PUBKEY" <<'EOF'
import sys, base64, pathlib
out, pubkey = sys.argv[1], sys.argv[2]
def b64(p): return base64.b64encode(pathlib.Path(p).read_bytes()).decode()
units = {
 "/etc/systemd/system/lab-net.service": """[Unit]
Description=Netwatch lab: fake home network
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/lab-net
[Install]
WantedBy=multi-user.target
""",
 "/etc/systemd/system/lab-devices.service": """[Unit]
Description=Netwatch lab: fake household devices
After=lab-net.service
Requires=lab-net.service
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/lab-devices up
ExecStop=/usr/local/sbin/lab-devices down
[Install]
WantedBy=multi-user.target
""",
}
y = f"""#cloud-config
hostname: netwatch-lab
users:
  - name: ubuntu
    groups: [sudo]
    shell: /bin/bash
    sudo: ALL=(ALL) NOPASSWD:ALL
    ssh_authorized_keys: [{pubkey}]
ssh_pwauth: false
disk_setup:
  /dev/vdb: {{table_type: gpt, layout: true, overwrite: false}}
fs_setup:
  - {{label: labdata, filesystem: ext4, device: /dev/vdb, partition: auto}}
mounts:
  - [LABEL=labdata, /var/lib/docker, ext4, "defaults,nofail", "0", "2"]
package_update: true
packages: [isc-dhcp-client, dnsmasq-base, nftables, dnsutils, curl, rsync, jq]
write_files:
  - {{path: /usr/local/sbin/lab-net, permissions: '0755', encoding: b64, content: {b64('vm/lab-net')}}}
  - {{path: /usr/local/sbin/lab-devices, permissions: '0755', encoding: b64, content: {b64('vm/lab-devices')}}}
  - {{path: /usr/local/sbin/lab-router, permissions: '0755', encoding: b64, content: {b64('vm/lab-router')}}}
"""
for path, body in units.items():
    y += f"  - {{path: {path}, permissions: '0644', encoding: b64, content: {base64.b64encode(body.encode()).decode()}}}\n"
y += """bootcmd:
  - [mkdir, -p, /var/lib/containerd]
runcmd:
  # Docker 29 keeps images in /var/lib/containerd; put it on the 8 GB data disk, not the small root disk
  - [sh, -c, "mkdir -p /var/lib/docker/.containerd; cp -a /var/lib/containerd/. /var/lib/docker/.containerd/ 2>/dev/null; rm -rf /var/lib/containerd/*; grep -q /var/lib/containerd /etc/fstab || echo '/var/lib/docker/.containerd /var/lib/containerd none bind,nofail,x-systemd.requires-mounts-for=/var/lib/docker 0 0' >> /etc/fstab; systemctl daemon-reload; mount /var/lib/containerd"]
  - [sh, -c, "DEBIAN_FRONTEND=noninteractive apt-get -y -qq purge 'linux-headers-*' && apt-get -y -qq autoremove && apt-get clean"]
  - [sh, -c, "mkdir -p /etc/systemd/journald.conf.d && printf '[Journal]\\nSystemMaxUse=50M\\n' > /etc/systemd/journald.conf.d/lab.conf && systemctl restart systemd-journald"]
  - [systemctl, daemon-reload]
  - [systemctl, enable, lab-net.service, lab-devices.service]
  - [systemctl, start, lab-net.service]
  - [systemctl, start, lab-devices.service]
final_message: "netwatch-lab ready after $UPTIME s"
"""
pathlib.Path(out).write_text(y)
EOF
hdiutil makehybrid -quiet -iso -joliet -default-volume-name cidata -o "$WORK/seed.iso" "$WORK/seed"

# --- the VM: 2 CPUs, 2 GB RAM, the cloud image, an 8 GB data disk, shared (NAT) network ---
cp "$IMG" "$WORK/root.qcow2"
osascript <<EOF
tell application "UTM"
  set rootDisk to POSIX file "$WORK/root.qcow2"
  set seedIso to POSIX file "$WORK/seed.iso"
  make new virtual machine with properties {backend:qemu, configuration:{name:"$NAME", architecture:"aarch64", memory:2048, cpu cores:2, notes:"Netwatch sandbox: fake home LAN 10.77.0.0/24 inside, isolated from the real network.", drives:{{interface:VirtIO, source:rootDisk}, {interface:VirtIO, guest size:8192}, {interface:VirtIO, source:seedIso}}, network interfaces:{{hardware:"virtio-net-pci", mode:shared, address:"$MAC"}}}}
end tell
EOF
echo "Created UTM VM $NAME. Start it with: $UTMCTL start $NAME"
