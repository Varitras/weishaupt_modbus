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

Its optional web interface entry logs in to the controller's web interface
over plain HTTP - the controller offers nothing else - and reads pages; the
only thing it writes there is the heating power limit, on an explicit user
action and under a daily limit. In scope there too: the user and password
going anywhere but the controller, a setting written that the user did not
ask for, and a power map picture that loads or runs anything once copied
under `www/`.
