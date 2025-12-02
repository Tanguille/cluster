# How to Check OPNsense BGP Configuration

## Steps to Verify BGP Settings in OPNsense

### 1. Check BGP ASN

1. Log into OPNsense web interface
2. Navigate to **Routing > BGP**
3. Check the **AS Number** field - it should be `64512` (or whatever you configured)
4. Make sure **Enable** is checked

### 2. Check BGP Neighbors/Peers

1. In the same BGP section, go to the **Neighbors** tab
2. You should see entries for your Kubernetes nodes:
   - `192.168.0.11` (control-1) with ASN `64513`
   - `192.168.0.12` (control-2) with ASN `64513`
   - `192.168.0.13` (control-3) with ASN `64513`
3. Each should have:
   - **Enabled**: ✓ checked
   - **Next-Hop-Self**: ✓ checked
   - **AS Number**: `64513` (your cluster ASN)

### 3. Check BGP Status

1. Navigate to **Routing > BGP > Status**
2. You should see BGP sessions with status "Established" for each node
3. If sessions are not established, check:
   - Firewall rules allow BGP traffic (TCP port 179)
   - Node IPs are correct
   - ASNs match between OPNsense and Cilium

### 4. Check BGP Routes

1. Navigate to **Routing > BGP > Routes**
2. After Cilium starts advertising, you should see routes for LoadBalancer IPs in the `192.168.69.0/24` range

## Quick Verification Commands

If you have SSH access to OPNsense, you can also check:

```bash
# Check BGP status
vtysh -c "show ip bgp summary"

# Check BGP neighbors
vtysh -c "show ip bgp neighbors"

# Check BGP routes
vtysh -c "show ip bgp"
```

## Expected Configuration

**OPNsense:**

- ASN: `64512`
- BGP enabled: Yes
- Neighbors: 192.168.0.11, 192.168.0.12, 192.168.0.13 (all with ASN 64513)

**Cilium:**

- Local ASN: `64513`
- Peer ASN: `64512`
- Peer Address: `192.168.0.1`
