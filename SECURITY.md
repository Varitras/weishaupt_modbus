# Security policy

## Supported versions

The latest release. This is a spare-time fork of one Home Assistant
integration, so older versions get no fixes - update before reporting.

## Reporting a vulnerability

Report privately through GitHub's
[security advisories](https://github.com/Varitras/weishaupt_modbus/security/advisories/new)
rather than in a public issue, and allow some time for a reply.

Please include what an attacker gains and how to reproduce it.

## Scope

This integration talks Modbus TCP to a heat pump on the local network. Modbus
has no authentication or encryption of its own, and anyone who can reach the
controller can read and write its registers - that is the protocol, not a
vulnerability in this integration. What is in scope: this code doing something
with the connection, the configuration or the register table that it should
not, such as writing a register the user did not ask for, or exposing
credentials or the network configuration of the installation.

Registers are written only on an explicit user action, and the controller's
EEPROM has a limited write budget, which the integration counts and caps.
