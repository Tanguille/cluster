# OPNsense BGP Configuration

Follow these steps to configure BGP peering between OPNsense and your Kubernetes cluster.

## Prerequisites

- OPNsense with `os-frr` plugin installed
- Access to OPNsense web interface

## Configuration Steps

### 1. Install os-frr Plugin

1. Log into OPNsense web interface
2. Navigate to **System > Firmware > Plugins**
3. Search for `os-frr` and install it
4. Refresh the webpage after installation

### 2. Initialize BGP Settings

1. Navigate to **Routing > BGP** in the sidebar
2. Set the **AS Number** to `64512` (OPNsense ASN)
3. Check the **Enable** checkbox
4. Click **Save**

### 3. Configure BGP Neighbors (Peers)

1. Navigate to the **Neighbors** tab in BGP settings
2. Click the **+** button to add each Kubernetes node

For each node (control-1, control-2, control-3), configure:

- **Enabled**: ✓ (checked)
- **Next-Hop-Self**: ✓ (checked)
- **IP Address**: Node IP address
  - control-1: `192.168.0.11`
  - control-2: `192.168.0.12`
  - control-3: `192.168.0.13`
- **AS Number**: `64513` (Cluster ASN)
- **Update-Source-Interface**: Select the interface that acts as the gateway for your Kubernetes nodes (usually your LAN interface)

3. Click **Save** after configuring each node
4. Repeat for all three nodes

## Verification

After configuring OPNsense and applying the Cilium BGP configuration:

1. Check BGP status in OPNsense: **Routing > BGP > Status**
2. You should see established BGP sessions with all three nodes
3. Check routes: **Routing > BGP > Routes** - you should see routes for LoadBalancer IPs (192.168.0.4, 192.168.0.6, 192.168.0.7, etc.)

## Troubleshooting

- If BGP sessions don't establish, check firewall rules allow BGP traffic (port 179)
- Verify node IPs are correct
- Check Cilium logs: `kubectl logs -n kube-system -l name=cilium-operator`
- Check BGP status in Cilium: `kubectl get ciliumbgppeeringpolicy bgp-peering-policy -o yaml`

