# The Netwatch lab

Netwatch changes how a network works, so test changes here instead of on your home network. The lab is
one Ubuntu VM with a whole fake home network inside it:

```
your computer ── VM network (NAT, isolated from your real network)
  └─ VM "netwatch-lab"
       ├─ netns isp-router  10.77.0.254   the home router: DHCP, DNS, NAT to the internet
       │                                  (forwards ports 22 and 8088 to the VM, so you can reach it)
       └─ lab-br 10.77.0.0/24             the home network
            ├─ 10.77.0.1                  the VM itself: where you install Netwatch
            └─ phone / tv / laptop        fake devices that get an address by DHCP and browse the web
```

Nothing in it can reach your real network's devices, and you can delete it and start again in minutes.

## Build it (macOS, UTM)

You need [UTM](https://mac.getutm.app) on an Apple Silicon Mac.

1. Download the Ubuntu 24.04 cloud image, `ubuntu-24.04-server-cloudimg-arm64.img`, from
   https://cloud-images.ubuntu.com/releases/noble/release/, and check it against `SHA256SUMS` there.
2. `./create-vm.sh path/to/ubuntu-24.04-server-cloudimg-arm64.img`
   (it uses your `~/.ssh/id_ed25519.pub` for logging in).
3. `/Applications/UTM.app/Contents/MacOS/utmctl start netwatch-lab`, and give it about 5 minutes the
   first time.
4. Find its address: the `netwatch-lab` entry with `hw_address=1,2:4e:57:1a:b0:1` in
   `/var/db/dhcpd_leases`. Then `ssh ubuntu@<that address>`.

**Turn off VPN apps while the VM downloads things.** Many of them break the VM's internet connection
(ping works, but nothing else does).

On Linux, the scripts in `vm/` work in any Ubuntu 24.04 VM: copy them to `/usr/local/sbin/` and run
`sudo lab-net && sudo lab-devices up`.

## Use it

Install Netwatch from your working copy, exactly as a user would install it from GitHub:

```sh
rsync -az --exclude .git --exclude data ../ ubuntu@<vm>:netwatch-src/
ssh -t ubuntu@<vm> 'sudo NETWATCH_SRC=$HOME/netwatch-src bash netwatch-src/install.sh'
ssh -f -N -L 127.0.0.1:8088:10.77.0.1:8088 ubuntu@<vm>     # then open http://localhost:8088
```

Inside the VM:

```sh
sudo lab-devices status                 # the fake devices and their addresses
sudo lab-devices add guest-phone        # a new device joins (try new-device approval)
sudo lab-devices remove guest-phone
sudo lab-devices down && sudo lab-devices up    # everyone reconnects (like toggling Wi-Fi)
sudo lab-router dhcp off                # step 2 of the gateway setup guide: "turn off your router's DHCP"
sudo lab-router dhcp on                 # and back
```

The VM's own address is fixed, so step 1 of the gateway guide (`sudo netwatch static-ip`) already
shows as done.
