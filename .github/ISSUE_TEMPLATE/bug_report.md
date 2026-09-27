---
name: Bug report
about: Something in the integration does not work as described
title: ''
labels: ''
assignees: ''

---

**Describe the bug**
What happened, and what you expected instead.

**To reproduce**
The steps, the entity or register involved, and whether it happens every
time.

**Versions**
 - Home Assistant: [e.g. 2026.9.3]
 - Weishaupt Modbus integration: [e.g. 2.0.2b2]
 - Heat pump model, and the controller firmware if you know it: [e.g. WBB 12]

**Connection**
How Home Assistant reaches the heat pump (same network, VLAN, VPN), and
whether anything else talks Modbus to it (a YAML `modbus:` hub, another
tool).

**Diagnostics and log**
Attach *Download diagnostics* from the entry's menu; it leaves out the
heat pump's address and the names you typed. A debug log (see the README,
*Troubleshooting*) helps too, but it is not cleaned: before attaching it,
replace IP addresses and host names, your user name in file paths, and
any names of people or rooms.

**Additional context**
Anything else, such as when it started or what changed before.
