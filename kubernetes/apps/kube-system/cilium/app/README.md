# Cilium

## OPNsense BGP

```sh
router bgp 64513
  bgp router-id 192.168.0.1
  no bgp ebgp-requires-policy

  neighbor k8s peer-group
  neighbor k8s remote-as 64512

  neighbor 192.168.0.11 peer-group k8s
  neighbor 192.168.0.12 peer-group k8s
  neighbor 192.168.0.13 peer-group k8s

  address-family ipv4 unicast
    neighbor k8s next-hop-self
  exit-address-family
exit
```
